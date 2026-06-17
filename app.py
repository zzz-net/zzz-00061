from flask import Flask, request, jsonify, g, send_file
from flask_cors import CORS
import sqlite3
import json
import csv
import io
import os
from datetime import datetime

DATABASE = 'archive_transfer.db'

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

BATCH_STATUS = {
    'REGISTERED': '已登记',
    'BOXED': '已装盒',
    'TRANSFERRED': '已移交',
    'SIGNED': '已签收',
    'REJECTED': '已退回'
}

BOX_STATUS = {
    'EMPTY': '空盒',
    'PACKED': '已装盒',
    'TRANSFERRED': '已移交',
    'SIGNED': '已签收',
    'REJECTED': '已退回'
}

USER_ROLES = {
    'SENDER': '发送方',
    'RECEIVER': '接收方'
}

REVIEW_STATUS = {
    'OPEN': '待处理',
    'IN_PROGRESS': '处理中',
    'PENDING_CLOSE': '申请关闭',
    'CLOSED': '已关闭',
    'REJECTED': '已退回'
}

REVIEW_STATUS_NAME = {
    'OPEN': '待处理',
    'IN_PROGRESS': '处理中',
    'PENDING_CLOSE': '申请关闭待确认',
    'CLOSED': '已关闭',
    'REJECTED': '已退回（需重新提报）'
}

URGENCY_LEVELS = {
    'NORMAL': '普通',
    'URGENT': '紧急',
    'CRITICAL': '特急'
}

URGENCY_ORDER = ['NORMAL', 'URGENT', 'CRITICAL']

REMINDER_STATUS = {
    'PENDING': '待处理',
    'PROCESSED': '已处理',
    'MERGED': '已合并',
    'CANCELLED': '已取消'
}

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  role TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS batches
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  batch_no TEXT UNIQUE NOT NULL,
                  description TEXT,
                  status TEXT NOT NULL DEFAULT 'REGISTERED',
                  created_by INTEGER NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (created_by) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS archives
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  archive_no TEXT NOT NULL,
                  batch_id INTEGER NOT NULL,
                  title TEXT NOT NULL,
                  remark TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (batch_id) REFERENCES batches(id),
                  UNIQUE(batch_id, archive_no))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS boxes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  box_no TEXT UNIQUE NOT NULL,
                  batch_id INTEGER,
                  status TEXT NOT NULL DEFAULT 'EMPTY',
                  signed_by INTEGER,
                  signed_at TIMESTAMP,
                  prev_status TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (batch_id) REFERENCES batches(id),
                  FOREIGN KEY (signed_by) REFERENCES users(id))''')
    
    for col in ['signed_by', 'signed_at', 'prev_status']:
        try:
            c.execute(f'ALTER TABLE boxes ADD COLUMN {col}')
        except sqlite3.OperationalError:
            pass
    
    c.execute('''CREATE TABLE IF NOT EXISTS archive_box_mapping
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  archive_id INTEGER NOT NULL,
                  box_id INTEGER NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (archive_id) REFERENCES archives(id),
                  FOREIGN KEY (box_id) REFERENCES boxes(id),
                  UNIQUE(archive_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS transfer_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  batch_id INTEGER,
                  box_id INTEGER,
                  action TEXT NOT NULL,
                  operator_id INTEGER NOT NULL,
                  operator_role TEXT NOT NULL,
                  box_no TEXT,
                  reason TEXT,
                  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (batch_id) REFERENCES batches(id),
                  FOREIGN KEY (box_id) REFERENCES boxes(id),
                  FOREIGN KEY (operator_id) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS export_records
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  batch_id INTEGER NOT NULL,
                  file_name TEXT NOT NULL,
                  content TEXT NOT NULL,
                  exported_by INTEGER NOT NULL,
                  exported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (batch_id) REFERENCES batches(id),
                  FOREIGN KEY (exported_by) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS review_items
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  batch_id INTEGER NOT NULL,
                  box_id INTEGER NOT NULL,
                  issue_type TEXT NOT NULL,
                  issue_description TEXT NOT NULL,
                  responsible_party TEXT,
                  handling_note TEXT,
                  deadline TIMESTAMP,
                  status TEXT NOT NULL DEFAULT 'OPEN',
                  created_by INTEGER NOT NULL,
                  closed_by INTEGER,
                  closed_at TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (batch_id) REFERENCES batches(id),
                  FOREIGN KEY (box_id) REFERENCES boxes(id),
                  FOREIGN KEY (created_by) REFERENCES users(id),
                  FOREIGN KEY (closed_by) REFERENCES users(id))''')
    
    for col in ['issue_type', 'responsible_party', 'handling_note', 'deadline', 
                'closed_by', 'closed_at', 'status']:
        try:
            c.execute(f'ALTER TABLE review_items ADD COLUMN {col}')
        except sqlite3.OperationalError:
            pass
    
    c.execute('''CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    expected_completion TEXT,
                    is_escalated INTEGER NOT NULL DEFAULT 0,
                    urgency TEXT NOT NULL DEFAULT 'NORMAL',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_by INTEGER,
                    processed_at TIMESTAMP,
                    process_note TEXT,
                    merged_into INTEGER,
                    FOREIGN KEY (review_id) REFERENCES review_items(id),
                    FOREIGN KEY (created_by) REFERENCES users(id),
                    FOREIGN KEY (processed_by) REFERENCES users(id),
                    FOREIGN KEY (merged_into) REFERENCES reminders(id))''')
    
    for col in ['expected_completion', 'is_escalated', 'urgency', 'status',
                'processed_by', 'processed_at', 'process_note', 'merged_into']:
        try:
            c.execute(f'ALTER TABLE reminders ADD COLUMN {col}')
        except sqlite3.OperationalError:
            pass
    
    c.execute('''CREATE TABLE IF NOT EXISTS reminder_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reminder_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    operator_id INTEGER NOT NULL,
                    detail TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (reminder_id) REFERENCES reminders(id),
                    FOREIGN KEY (operator_id) REFERENCES users(id))''')
    
    for col in ['detail']:
        try:
            c.execute(f'ALTER TABLE reminder_logs ADD COLUMN {col}')
        except sqlite3.OperationalError:
            pass
    
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (username, role) VALUES (?, ?)", ('sender', 'SENDER'))
        c.execute("INSERT INTO users (username, role) VALUES (?, ?)", ('receiver', 'RECEIVER'))
    
    conn.commit()
    conn.close()

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def add_history(db, batch_id, box_id, action, operator_id, operator_role, box_no=None, reason=None):
    db.execute('''INSERT INTO transfer_history 
                  (batch_id, box_id, action, operator_id, operator_role, box_no, reason)
                  VALUES (?, ?, ?, ?, ?, ?, ?)''',
               (batch_id, box_id, action, operator_id, operator_role, box_no, reason))

def max_urgency(a, b):
    ai = URGENCY_ORDER.index(a) if a in URGENCY_ORDER else 0
    bi = URGENCY_ORDER.index(b) if b in URGENCY_ORDER else 0
    return URGENCY_ORDER[max(ai, bi)]

def enrich_reminder(db, rm):
    rm['status_name'] = REMINDER_STATUS.get(rm['status'], rm['status'])
    rm['urgency_name'] = URGENCY_LEVELS.get(rm['urgency'], rm['urgency'])
    creator = db.execute('SELECT username FROM users WHERE id = ?', (rm['created_by'],)).fetchone()
    rm['creator_name'] = creator['username'] if creator else None
    if rm.get('processed_by'):
        processor = db.execute('SELECT username FROM users WHERE id = ?', (rm['processed_by'],)).fetchone()
        rm['processor_name'] = processor['username'] if processor else None
    else:
        rm['processor_name'] = None
    return rm

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/api/users', methods=['GET'])
def get_users():
    db = get_db()
    db.row_factory = dict_factory
    users = db.execute('SELECT * FROM users').fetchall()
    for u in users:
        u['role_name'] = USER_ROLES.get(u['role'], u['role'])
    return jsonify(users)

@app.route('/api/batches', methods=['GET'])
def get_batches():
    db = get_db()
    db.row_factory = dict_factory
    batches = db.execute('''SELECT b.*, u.username as creator_name,
                           (SELECT COUNT(*) FROM archives a WHERE a.batch_id = b.id) as archive_count
                           FROM batches b JOIN users u ON b.created_by = u.id
                           ORDER BY b.created_at DESC''').fetchall()
    for b in batches:
        b['status_name'] = BATCH_STATUS.get(b['status'], b['status'])
    return jsonify(batches)

@app.route('/api/batches/<int:batch_id>', methods=['GET'])
def get_batch_detail(batch_id):
    db = get_db()
    db.row_factory = dict_factory
    
    batch = db.execute('''SELECT b.*, u.username as creator_name
                         FROM batches b JOIN users u ON b.created_by = u.id
                         WHERE b.id = ?''', (batch_id,)).fetchone()
    if not batch:
        return jsonify({'error': '批次不存在'}), 404
    
    batch['status_name'] = BATCH_STATUS.get(batch['status'], batch['status'])
    
    archives = db.execute('''SELECT a.*, m.box_id, bx.box_no
                            FROM archives a 
                            LEFT JOIN archive_box_mapping m ON a.id = m.archive_id
                            LEFT JOIN boxes bx ON m.box_id = bx.id
                            WHERE a.batch_id = ?
                            ORDER BY a.created_at''', (batch_id,)).fetchall()
    
    boxes = db.execute('''SELECT bx.*,
                         (SELECT COUNT(*) FROM archive_box_mapping m WHERE m.box_id = bx.id) as archive_count
                         FROM boxes bx WHERE bx.batch_id = ?
                         ORDER BY bx.created_at''', (batch_id,)).fetchall()
    for bx in boxes:
        bx['status_name'] = BOX_STATUS.get(bx['status'], bx['status'])
    
    history = db.execute('''SELECT h.*, u.username as operator_name
                           FROM transfer_history h JOIN users u ON h.operator_id = u.id
                           WHERE h.batch_id = ?
                           ORDER BY h.timestamp DESC''', (batch_id,)).fetchall()
    
    reviews = db.execute('''SELECT r.*, bx.box_no, u.username as creator_name,
                           uc.username as closer_name
                           FROM review_items r
                           JOIN boxes bx ON r.box_id = bx.id
                           JOIN users u ON r.created_by = u.id
                           LEFT JOIN users uc ON r.closed_by = uc.id
                           WHERE r.batch_id = ?
                           ORDER BY r.created_at DESC''', (batch_id,)).fetchall()
    for rv in reviews:
        rv['status_name'] = REVIEW_STATUS_NAME.get(rv['status'], rv['status'])
        reminder_stats = db.execute('''SELECT COUNT(*) as total,
            SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN is_escalated = 1 AND status = 'PENDING' THEN 1 ELSE 0 END) as escalated_pending,
            SUM(CASE WHEN status = 'PROCESSED' THEN 1 ELSE 0 END) as processed
            FROM reminders WHERE review_id = ?''', (rv['id'],)).fetchone()
        rv['reminder_total'] = reminder_stats['total']
        rv['reminder_pending'] = reminder_stats['pending']
        rv['reminder_escalated'] = reminder_stats['escalated_pending']
        rv['reminder_processed'] = reminder_stats['processed']
        last_reminder = db.execute('''SELECT r.*, u.username as creator_name
            FROM reminders r JOIN users u ON r.created_by = u.id
            WHERE r.review_id = ? AND r.status != 'MERGED' AND (r.merged_into IS NULL OR r.merged_into = 0)
            ORDER BY r.created_at DESC LIMIT 1''', (rv['id'],)).fetchone()
        if last_reminder:
            rv['last_reminder_by'] = last_reminder['creator_name']
            rv['last_reminder_at'] = last_reminder['created_at']
            rv['last_reminder_urgency'] = last_reminder['urgency']
            rv['last_reminder_urgency_name'] = URGENCY_LEVELS.get(last_reminder['urgency'], last_reminder['urgency'])
    
    review_summary = {}
    for bx in boxes:
        box_reviews = [r for r in reviews if r['box_id'] == bx['id']]
        review_summary[str(bx['id'])] = {
            'box_id': bx['id'],
            'total': len(box_reviews),
            'open': len([r for r in box_reviews if r['status'] in ('OPEN', 'IN_PROGRESS', 'PENDING_CLOSE', 'REJECTED')]),
            'closed': len([r for r in box_reviews if r['status'] == 'CLOSED'])
        }
    
    return jsonify({
        'batch': batch,
        'archives': archives,
        'boxes': boxes,
        'history': history,
        'reviews': reviews,
        'review_summary': review_summary
    })

@app.route('/api/batches', methods=['POST'])
def create_batch():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    try:
        cursor = db.execute('''INSERT INTO batches (batch_no, description, created_by)
                              VALUES (?, ?, ?)''',
                           (data['batch_no'], data.get('description', ''), data['created_by']))
        batch_id = cursor.lastrowid
        
        archives = data.get('archives', [])
        for arch in archives:
            db.execute('''INSERT INTO archives (archive_no, batch_id, title, remark)
                         VALUES (?, ?, ?, ?)''',
                      (arch['archive_no'], batch_id, arch['title'], arch.get('remark', '')))
        
        add_history(db, batch_id, None, '批次登记', data['created_by'], 
                   db.execute('SELECT role FROM users WHERE id = ?', (data['created_by'],)).fetchone()['role'],
                   reason=f'创建批次，共{len(archives)}份档案')
        
        db.commit()
        
        batch = db.execute('''SELECT b.*, u.username as creator_name,
                             (SELECT COUNT(*) FROM archives a WHERE a.batch_id = b.id) as archive_count
                             FROM batches b JOIN users u ON b.created_by = u.id
                             WHERE b.id = ?''', (batch_id,)).fetchone()
        batch['status_name'] = BATCH_STATUS.get(batch['status'], batch['status'])
        
        return jsonify(batch)
    except sqlite3.IntegrityError as e:
        db.rollback()
        err_msg = str(e)
        if 'UNIQUE constraint failed: batches.batch_no' in err_msg:
            return jsonify({'error': '批次号已存在'}), 400
        elif 'UNIQUE constraint failed: archives.batch_id, archives.archive_no' in err_msg:
            return jsonify({'error': '同一批次中存在重复档号，登记已拒绝'}), 400
        return jsonify({'error': '数据错误'}), 400

@app.route('/api/batches/<int:batch_id>/archives', methods=['POST'])
def add_archive(batch_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    batch = db.execute('SELECT * FROM batches WHERE id = ?', (batch_id,)).fetchone()
    if not batch:
        return jsonify({'error': '批次不存在'}), 404
    if batch['status'] != 'REGISTERED':
        return jsonify({'error': '只能在"已登记"状态下添加档案'}), 400
    
    try:
        db.execute('''INSERT INTO archives (archive_no, batch_id, title, remark)
                     VALUES (?, ?, ?, ?)''',
                  (data['archive_no'], batch_id, data['title'], data.get('remark', '')))
        
        add_history(db, batch_id, None, '添加档案', data['operator_id'],
                   db.execute('SELECT role FROM users WHERE id = ?', (data['operator_id'],)).fetchone()['role'],
                   reason=f'添加档案: {data["archive_no"]} - {data["title"]}')
        
        db.commit()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'error': '该批次中已存在相同档号，添加已拒绝'}), 400

@app.route('/api/boxes', methods=['POST'])
def create_box():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    try:
        cursor = db.execute('''INSERT INTO boxes (box_no, batch_id, status)
                              VALUES (?, ?, ?)''',
                           (data['box_no'], data['batch_id'], 'EMPTY'))
        box_id = cursor.lastrowid
        
        add_history(db, data['batch_id'], box_id, '创建档案盒', data['operator_id'],
                   db.execute('SELECT role FROM users WHERE id = ?', (data['operator_id'],)).fetchone()['role'],
                   box_no=data['box_no'],
                   reason=f'创建档案盒: {data["box_no"]}')
        
        db.commit()
        
        box = db.execute('''SELECT bx.*,
                           (SELECT COUNT(*) FROM archive_box_mapping m WHERE m.box_id = bx.id) as archive_count
                           FROM boxes bx WHERE bx.id = ?''', (box_id,)).fetchone()
        box['status_name'] = BOX_STATUS.get(box['status'], box['status'])
        
        return jsonify(box)
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'error': '盒号已存在'}), 400

@app.route('/api/boxes/pack', methods=['POST'])
def pack_archives_to_box():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    batch_id = data['batch_id']
    box_id = data['box_id']
    archive_ids = data['archive_ids']
    operator_id = data['operator_id']
    
    batch = db.execute('SELECT * FROM batches WHERE id = ?', (batch_id,)).fetchone()
    if not batch:
        return jsonify({'error': '批次不存在'}), 404
    if batch['status'] not in ['REGISTERED', 'BOXED']:
        return jsonify({'error': '当前批次状态不允许装盒'}), 400
    
    box = db.execute('SELECT * FROM boxes WHERE id = ? AND batch_id = ?', (box_id, batch_id)).fetchone()
    if not box:
        return jsonify({'error': '档案盒不存在或不属于该批次'}), 404
    if box['status'] not in ['EMPTY', 'PACKED']:
        return jsonify({'error': '档案盒已移交，不能再装盒'}), 400
    
    operator_role = db.execute('SELECT role FROM users WHERE id = ?', (operator_id,)).fetchone()['role']
    
    for aid in archive_ids:
        existing = db.execute('SELECT * FROM archive_box_mapping WHERE archive_id = ?', (aid,)).fetchone()
        if existing and existing['box_id'] != box_id:
            db.rollback()
            return jsonify({'error': f'档案ID {aid} 已装入其他盒子'}), 400
        
        if not existing:
            db.execute('''INSERT INTO archive_box_mapping (archive_id, box_id)
                         VALUES (?, ?)''', (aid, box_id))
    
    db.execute("UPDATE boxes SET status = 'PACKED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (box_id,))
    
    all_boxes = db.execute("SELECT * FROM boxes WHERE batch_id = ?", (batch_id,)).fetchall()
    all_archives = db.execute("SELECT * FROM archives WHERE batch_id = ?", (batch_id,)).fetchall()
    packed_count = db.execute("SELECT COUNT(*) as cnt FROM archive_box_mapping m JOIN boxes b ON m.box_id = b.id WHERE b.batch_id = ?", (batch_id,)).fetchone()['cnt']
    
    if packed_count == len(all_archives) and len(all_boxes) > 0:
        db.execute("UPDATE batches SET status = 'BOXED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (batch_id,))
        add_history(db, batch_id, None, '批次装盒完成', operator_id, operator_role,
                   reason=f'所有{len(all_archives)}份档案已全部装入{len(all_boxes)}个盒子')
    
    add_history(db, batch_id, box_id, '装入档案', operator_id, operator_role,
               box_no=box['box_no'],
               reason=f'装入{len(archive_ids)}份档案到盒子 {box["box_no"]}')
    
    db.commit()
    return jsonify({'success': True})

@app.route('/api/batches/<int:batch_id>/transfer', methods=['POST'])
def transfer_batch(batch_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    batch = db.execute('SELECT * FROM batches WHERE id = ?', (batch_id,)).fetchone()
    if not batch:
        return jsonify({'error': '批次不存在'}), 404
    if batch['status'] != 'BOXED':
        return jsonify({'error': '批次必须完成装盒后才能发起移交'}), 400
    
    operator_id = data['operator_id']
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    all_archives = db.execute("SELECT * FROM archives WHERE batch_id = ?", (batch_id,)).fetchall()
    packed_count = db.execute("SELECT COUNT(*) as cnt FROM archive_box_mapping m JOIN boxes b ON m.box_id = b.id WHERE b.batch_id = ?", (batch_id,)).fetchone()['cnt']
    
    if packed_count < len(all_archives):
        return jsonify({'error': '还有档案未装盒，不能发起移交'}), 400
    
    db.execute("UPDATE batches SET status = 'TRANSFERRED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (batch_id,))
    db.execute("UPDATE boxes SET status = 'TRANSFERRED', updated_at = CURRENT_TIMESTAMP WHERE batch_id = ?", (batch_id,))
    
    boxes = db.execute("SELECT * FROM boxes WHERE batch_id = ?", (batch_id,)).fetchall()
    for bx in boxes:
        add_history(db, batch_id, bx['id'], '发起移交', operator_id, operator['role'],
                   box_no=bx['box_no'],
                   reason=f'发起移交，盒子: {bx["box_no"]}')
    
    add_history(db, batch_id, None, '批次移交', operator_id, operator['role'],
               reason=f'批次 {batch["batch_no"]} 已发起移交，共{len(boxes)}个盒子')
    
    db.commit()
    return jsonify({'success': True})

@app.route('/api/boxes/<int:box_id>/sign', methods=['POST'])
def sign_box(box_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (box_id,)).fetchone()
    if not box:
        return jsonify({'error': '档案盒不存在'}), 404
    if box['status'] != 'TRANSFERRED':
        return jsonify({'error': '只有已移交状态的盒子才能签收'}), 400
    
    operator_id = data['operator_id']
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    
    if operator['role'] != 'RECEIVER':
        return jsonify({'error': '只有接收方账号才能签收，发送方不能代替接收方签收'}), 400
    
    db.execute("UPDATE boxes SET status = 'SIGNED', signed_by = ?, signed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (operator_id, box_id))
    
    signer_name = operator['username']
    add_history(db, box['batch_id'], box_id, '签收档案盒', operator_id, operator['role'],
               box_no=box['box_no'],
               reason=f'接收方 {signer_name} 已签收盒子: {box["box_no"]}')
    
    remaining = db.execute("SELECT COUNT(*) as cnt FROM boxes WHERE batch_id = ? AND status != 'SIGNED'", (box['batch_id'],)).fetchone()['cnt']
    if remaining == 0:
        db.execute("UPDATE batches SET status = 'SIGNED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (box['batch_id'],))
        add_history(db, box['batch_id'], None, '批次签收完成', operator_id, operator['role'],
                   reason='所有档案盒已签收，批次完成')
    
    db.commit()
    return jsonify({'success': True})

@app.route('/api/boxes/<int:box_id>/reject', methods=['POST'])
def reject_box(box_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (box_id,)).fetchone()
    if not box:
        return jsonify({'error': '档案盒不存在'}), 404
    if box['status'] not in ('TRANSFERRED', 'SIGNED'):
        return jsonify({'error': '只有已移交或已签收状态的盒子才能退回'}), 400
    
    operator_id = data['operator_id']
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    reason = data.get('reason', '')
    
    if operator['role'] != 'RECEIVER':
        return jsonify({'error': '只有接收方账号才能退回档案'}), 400
    
    if not reason:
        return jsonify({'error': '退回原因不能为空'}), 400
    
    prev_status = box['status']
    prev_signed_by = box.get('signed_by')
    prev_signed_at = box.get('signed_at')
    
    db.execute("UPDATE boxes SET status = 'REJECTED', prev_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", 
               (prev_status, box_id))
    db.execute("UPDATE batches SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (box['batch_id'],))
    
    status_map = {'TRANSFERRED': '已移交', 'SIGNED': '已签收'}
    prev_status_name = status_map.get(prev_status, prev_status)
    snapshot_info = f'退回前状态: {prev_status_name}'
    if prev_signed_by and prev_signed_at:
        signer = db.execute('SELECT username FROM users WHERE id = ?', (prev_signed_by,)).fetchone()
        signer_name = signer['username'] if signer else '未知'
        snapshot_info += f', 签收人: {signer_name}, 签收时间: {prev_signed_at}'
    
    add_history(db, box['batch_id'], box_id, '退回档案盒', operator_id, operator['role'],
               box_no=box['box_no'],
               reason=f'退回原因: {reason} | {snapshot_info}')
    
    db.commit()
    return jsonify({'success': True, 'prev_status': prev_status})

@app.route('/api/boxes/<int:box_id>/revoke-reject', methods=['POST'])
def revoke_reject(box_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (box_id,)).fetchone()
    if not box:
        return jsonify({'error': '档案盒不存在'}), 404
    if box['status'] != 'REJECTED':
        return jsonify({'error': '只有已退回状态的盒子才能撤销退回'}), 400
    
    operator_id = data['operator_id']
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    
    prev_status = box.get('prev_status') or 'TRANSFERRED'
    
    if prev_status == 'SIGNED':
        last_sign_history = db.execute('''SELECT * FROM transfer_history 
                                          WHERE box_id = ? AND action = '签收档案盒'
                                          ORDER BY timestamp DESC LIMIT 1''', (box_id,)).fetchone()
        if last_sign_history:
            signed_by = last_sign_history['operator_id']
            signed_at = last_sign_history['timestamp']
            db.execute("UPDATE boxes SET status = 'SIGNED', signed_by = ?, signed_at = ?, prev_status = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", 
                       (signed_by, signed_at, box_id))
            signer = db.execute('SELECT username FROM users WHERE id = ?', (signed_by,)).fetchone()
            signer_name = signer['username'] if signer else '未知'
            add_history(db, box['batch_id'], box_id, '撤销退回', operator_id, operator['role'],
                       box_no=box['box_no'],
                       reason=f'撤销退回，盒子 {box["box_no"]} 恢复为"已签收"状态，签收人: {signer_name}，签收时间: {signed_at}')
        else:
            db.execute("UPDATE boxes SET status = 'TRANSFERRED', prev_status = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (box_id,))
            add_history(db, box['batch_id'], box_id, '撤销退回', operator_id, operator['role'],
                       box_no=box['box_no'],
                       reason=f'撤销退回，未找到签收记录，盒子 {box["box_no"]} 恢复为"已移交"状态')
    else:
        db.execute("UPDATE boxes SET status = 'TRANSFERRED', prev_status = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (box_id,))
        add_history(db, box['batch_id'], box_id, '撤销退回', operator_id, operator['role'],
                   box_no=box['box_no'],
                   reason=f'撤销退回，盒子 {box["box_no"]} 恢复为"已移交"状态')
    
    all_boxes = db.execute("SELECT * FROM boxes WHERE batch_id = ?", (box['batch_id'],)).fetchall()
    all_rejected = all(bx['status'] == 'REJECTED' for bx in all_boxes)
    all_signed = all(bx['status'] == 'SIGNED' for bx in all_boxes)
    if all_rejected:
        db.execute("UPDATE batches SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (box['batch_id'],))
    elif all_signed:
        db.execute("UPDATE batches SET status = 'SIGNED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (box['batch_id'],))
    else:
        db.execute("UPDATE batches SET status = 'TRANSFERRED', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (box['batch_id'],))
    
    db.commit()
    return jsonify({'success': True, 'restored_status': prev_status})

@app.route('/api/history/<int:batch_id>', methods=['GET'])
def get_history(batch_id):
    db = get_db()
    db.row_factory = dict_factory
    history = db.execute('''SELECT h.*, u.username as operator_name
                           FROM transfer_history h JOIN users u ON h.operator_id = u.id
                           WHERE h.batch_id = ?
                           ORDER BY h.timestamp DESC''', (batch_id,)).fetchall()
    return jsonify(history)

@app.route('/api/batches/<int:batch_id>/export', methods=['GET', 'POST'])
def export_batch(batch_id):
    db = get_db()
    db.row_factory = dict_factory
    
    batch = db.execute('''SELECT b.*, u.username as creator_name
                         FROM batches b JOIN users u ON b.created_by = u.id
                         WHERE b.id = ?''', (batch_id,)).fetchone()
    if not batch:
        return jsonify({'error': '批次不存在'}), 404
    
    data = db.execute('''SELECT a.archive_no, a.title, a.remark, bx.box_no, bx.status
                        FROM archives a 
                        LEFT JOIN archive_box_mapping m ON a.id = m.archive_id
                        LEFT JOIN boxes bx ON m.box_id = bx.id
                        WHERE a.batch_id = ?
                        ORDER BY bx.box_no, a.archive_no''', (batch_id,)).fetchall()
    
    history = db.execute('''SELECT h.action, u.username as operator_name, h.timestamp, h.box_no, h.reason
                           FROM transfer_history h JOIN users u ON h.operator_id = u.id
                           WHERE h.batch_id = ?
                           ORDER BY h.timestamp''', (batch_id,)).fetchall()
    
    reviews = db.execute('''SELECT r.*, bx.box_no, u.username as creator_name, uc.username as closer_name
                           FROM review_items r
                           JOIN boxes bx ON r.box_id = bx.id
                           JOIN users u ON r.created_by = u.id
                           LEFT JOIN users uc ON r.closed_by = uc.id
                           WHERE r.batch_id = ?
                           ORDER BY bx.box_no, r.created_at''', (batch_id,)).fetchall()
    
    boxes_list = db.execute('SELECT * FROM boxes WHERE batch_id = ? ORDER BY created_at', (batch_id,)).fetchall()
    
    all_reminders = db.execute('''SELECT rm.*, u.username as creator_name, up.username as processor_name,
        r.issue_type, r.issue_description, bx.box_no
        FROM reminders rm
        JOIN users u ON rm.created_by = u.id
        LEFT JOIN users up ON rm.processed_by = up.id
        JOIN review_items r ON rm.review_id = r.id
        JOIN boxes bx ON r.box_id = bx.id
        WHERE r.batch_id = ? AND (rm.merged_into IS NULL OR rm.merged_into = 0)
        ORDER BY rm.created_at''', (batch_id,)).fetchall()
    total_reminders = len(all_reminders)
    pending_reminders = len([r for r in all_reminders if r['status'] == 'PENDING'])
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['档案移交清单'])
    writer.writerow([f'批次号: {batch["batch_no"]}'])
    writer.writerow([f'描述: {batch["description"]}'])
    writer.writerow([f'创建人: {batch["creator_name"]}'])
    writer.writerow([f'当前状态: {BATCH_STATUS.get(batch["status"], batch["status"])}'])
    writer.writerow([f'创建时间: {batch["created_at"]}'])
    
    total_reviews = len(reviews)
    open_reviews = len([r for r in reviews if r['status'] in ('OPEN', 'IN_PROGRESS', 'PENDING_CLOSE', 'REJECTED')])
    closed_reviews = len([r for r in reviews if r['status'] == 'CLOSED'])
    writer.writerow([f'复核项统计: 共{total_reviews}项, 待处理{open_reviews}项, 已关闭{closed_reviews}项'])
    writer.writerow([f'催办统计: 共{total_reminders}条, 待处理{pending_reminders}条'])
    
    writer.writerow([])
    writer.writerow(['档案详情'])
    writer.writerow(['档号', '题名', '备注', '盒号', '盒子状态'])
    for row in data:
        writer.writerow([
            row['archive_no'],
            row['title'],
            row['remark'] or '',
            row['box_no'] or '',
            BOX_STATUS.get(row['status'], row['status']) if row['status'] else ''
        ])
    
    writer.writerow([])
    writer.writerow(['复核摘要'])
    writer.writerow(['盒号', '复核项总数', '待处理数', '已关闭数', '复核项详情'])
    for bx in boxes_list:
        box_reviews = [r for r in reviews if r['box_id'] == bx['id']]
        box_open = len([r for r in box_reviews if r['status'] in ('OPEN', 'IN_PROGRESS', 'PENDING_CLOSE', 'REJECTED')])
        box_closed = len([r for r in box_reviews if r['status'] == 'CLOSED'])
        review_details = '; '.join([
            f"#{r['id']}[{REVIEW_STATUS_NAME.get(r['status'], r['status'])}] {r['issue_type']}:{r['issue_description'][:30]}"
            for r in box_reviews
        ]) if box_reviews else '-'
        writer.writerow([
            bx['box_no'],
            len(box_reviews),
            box_open,
            box_closed,
            review_details
        ])
    
    if reviews:
        writer.writerow([])
        writer.writerow(['复核项明细'])
        writer.writerow(['复核ID', '盒号', '问题类型', '问题描述', '责任方', '截止时间', '处理说明', '状态', '提报人', '提报时间', '关闭人', '关闭时间'])
        for r in reviews:
            writer.writerow([
                r['id'],
                r['box_no'],
                r['issue_type'],
                r['issue_description'],
                r['responsible_party'] or '',
                r['deadline'] or '',
                r['handling_note'] or '',
                REVIEW_STATUS_NAME.get(r['status'], r['status']),
                r['creator_name'],
                r['created_at'],
                r['closer_name'] or '',
                r['closed_at'] or ''
            ])
    
    if all_reminders:
        writer.writerow([])
        writer.writerow(['催办摘要'])
        writer.writerow(['催办ID', '复核项ID', '盒号', '问题类型', '催办原因', '紧急程度', '是否升级',
                         '期望完成时间', '状态', '催办人', '催办时间', '处理人', '处理时间', '处理备注'])
        for rm in all_reminders:
            writer.writerow([
                rm['id'],
                rm['review_id'],
                rm['box_no'],
                rm['issue_type'],
                rm['reason'],
                URGENCY_LEVELS.get(rm['urgency'], rm['urgency']),
                '是' if rm['is_escalated'] else '否',
                rm['expected_completion'] or '',
                REMINDER_STATUS.get(rm['status'], rm['status']),
                rm['creator_name'],
                rm['created_at'],
                rm['processor_name'] or '',
                rm['processed_at'] or '',
                rm['process_note'] or ''
            ])
    
    writer.writerow([])
    writer.writerow(['流转历史'])
    writer.writerow(['操作', '操作人', '时间', '盒号', '原因/备注'])
    for row in history:
        writer.writerow([
            row['action'],
            row['operator_name'],
            row['timestamp'],
            row['box_no'] or '',
            row['reason'] or ''
        ])
    
    content = output.getvalue()
    
    if request.method == 'POST':
        data_post = request.json
        cursor = db.execute('''INSERT INTO export_records (batch_id, file_name, content, exported_by)
                     VALUES (?, ?, ?, ?)''',
                  (batch_id, f'{batch["batch_no"]}_移交清单.csv', content, data_post['operator_id']))
        export_id = cursor.lastrowid
        db.commit()
        return jsonify({
            'success': True,
            'export_id': export_id,
            'file_name': f'{batch["batch_no"]}_移交清单.csv',
            'content': content
        })
    
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv; charset=utf-8-sig',
        as_attachment=True,
        download_name=f'{batch["batch_no"]}_移交清单.csv'
    )

@app.route('/api/export-records/<int:batch_id>', methods=['GET'])
def get_export_records(batch_id):
    db = get_db()
    db.row_factory = dict_factory
    records = db.execute('''SELECT e.*, u.username as exporter_name
                           FROM export_records e JOIN users u ON e.exported_by = u.id
                           WHERE e.batch_id = ?
                           ORDER BY e.exported_at DESC''', (batch_id,)).fetchall()
    return jsonify(records)

@app.route('/api/export-records/<int:export_id>/download', methods=['GET'])
def download_export_record(export_id):
    db = get_db()
    db.row_factory = dict_factory
    record = db.execute('SELECT * FROM export_records WHERE id = ?', (export_id,)).fetchone()
    if not record:
        return jsonify({'error': '导出记录不存在'}), 404
    return send_file(
        io.BytesIO(record['content'].encode('utf-8-sig')),
        mimetype='text/csv; charset=utf-8-sig',
        as_attachment=True,
        download_name=record['file_name']
    )

@app.route('/api/batches/<int:batch_id>/reviews', methods=['GET'])
def get_reviews(batch_id):
    db = get_db()
    db.row_factory = dict_factory
    
    batch = db.execute('SELECT * FROM batches WHERE id = ?', (batch_id,)).fetchone()
    if not batch:
        return jsonify({'error': '批次不存在'}), 404
    
    reviews = db.execute('''SELECT r.*, bx.box_no, u.username as creator_name,
                           uc.username as closer_name
                           FROM review_items r
                           JOIN boxes bx ON r.box_id = bx.id
                           JOIN users u ON r.created_by = u.id
                           LEFT JOIN users uc ON r.closed_by = uc.id
                           WHERE r.batch_id = ?
                           ORDER BY r.created_at DESC''', (batch_id,)).fetchall()
    for rv in reviews:
        rv['status_name'] = REVIEW_STATUS_NAME.get(rv['status'], rv['status'])
    return jsonify(reviews)

@app.route('/api/reviews', methods=['POST'])
def create_review():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    batch_id = data.get('batch_id')
    box_id = data.get('box_id')
    operator_id = data.get('operator_id')
    issue_type = data.get('issue_type', '').strip()
    issue_description = data.get('issue_description', '').strip()
    responsible_party = data.get('responsible_party', '').strip()
    deadline = data.get('deadline')
    
    batch = db.execute('SELECT * FROM batches WHERE id = ?', (batch_id,)).fetchone()
    if not batch:
        return jsonify({'error': '批次不存在'}), 404
    
    box = db.execute('SELECT * FROM boxes WHERE id = ? AND batch_id = ?', (box_id, batch_id)).fetchone()
    if not box:
        return jsonify({'error': '档案盒不存在或不属于该批次'}), 404
    
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    if operator['role'] != 'RECEIVER':
        return jsonify({'error': '只有接收方才能新建复核项'}), 400
    
    if not issue_type or not issue_description:
        return jsonify({'error': '问题类型和问题描述不能为空'}), 400
    
    existing = db.execute('''SELECT * FROM review_items 
                            WHERE box_id = ? AND issue_type = ? AND status != 'CLOSED'
                            ORDER BY created_at DESC LIMIT 1''',
                         (box_id, issue_type)).fetchone()
    if existing and existing['issue_description'].strip() == issue_description:
        return jsonify({'error': f'该盒子下已存在相同问题（状态：{REVIEW_STATUS_NAME.get(existing["status"], existing["status"])}），请勿重复提交。如有更新请编辑现有复核项'}), 409
    
    try:
        cursor = db.execute('''INSERT INTO review_items 
                              (batch_id, box_id, issue_type, issue_description, 
                               responsible_party, deadline, status, created_by)
                              VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)''',
                           (batch_id, box_id, issue_type, issue_description,
                            responsible_party or None, deadline or None, operator_id))
        review_id = cursor.lastrowid
        
        add_history(db, batch_id, box_id, '新建复核项', operator_id, operator['role'],
                   box_no=box['box_no'],
                   reason=f'复核项#{review_id} [{issue_type}] {issue_description}'
                   + (f' | 责任方: {responsible_party}' if responsible_party else '')
                   + (f' | 截止: {deadline}' if deadline else ''))
        
        db.commit()
        
        review = db.execute('''SELECT r.*, bx.box_no, u.username as creator_name
                              FROM review_items r
                              JOIN boxes bx ON r.box_id = bx.id
                              JOIN users u ON r.created_by = u.id
                              WHERE r.id = ?''', (review_id,)).fetchone()
        review['status_name'] = REVIEW_STATUS_NAME.get(review['status'], review['status'])
        return jsonify(review)
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'创建失败: {str(e)}'}), 500

@app.route('/api/reviews/<int:review_id>/update', methods=['POST'])
def update_review(review_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    review = db.execute('SELECT * FROM review_items WHERE id = ?', (review_id,)).fetchone()
    if not review:
        return jsonify({'error': '复核项不存在'}), 404
    
    operator_id = data.get('operator_id')
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    if operator['role'] != 'SENDER':
        return jsonify({'error': '只有发送方才能更新复核项处理结果'}), 400
    
    if review['status'] == 'CLOSED':
        return jsonify({'error': '已关闭的复核项不能更新，如需修改请先撤销重开'}), 400
    
    handling_note = data.get('handling_note', review.get('handling_note') or '')
    new_status = data.get('status', review['status'])
    
    if new_status not in ('IN_PROGRESS', 'PENDING_CLOSE'):
        return jsonify({'error': '发送方只能将状态更新为"处理中"或"申请关闭"'}), 400
    
    if new_status == 'PENDING_CLOSE' and not handling_note.strip():
        return jsonify({'error': '申请关闭前必须填写处理说明'}), 400
    
    old_status_name = REVIEW_STATUS_NAME.get(review['status'], review['status'])
    new_status_name = REVIEW_STATUS_NAME.get(new_status, new_status)
    
    db.execute('''UPDATE review_items 
                 SET handling_note = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?''',
              (handling_note or None, new_status, review_id))
    
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (review['box_id'],)).fetchone()
    add_history(db, review['batch_id'], review['box_id'], '更新复核项', operator_id, operator['role'],
               box_no=box['box_no'] if box else None,
               reason=f'复核项#{review_id} 状态更新: {old_status_name} → {new_status_name}'
               + (f' | 处理说明: {handling_note}' if handling_note else ''))
    
    db.commit()
    
    review = db.execute('''SELECT r.*, bx.box_no, u.username as creator_name,
                          uc.username as closer_name
                          FROM review_items r
                          JOIN boxes bx ON r.box_id = bx.id
                          JOIN users u ON r.created_by = u.id
                          LEFT JOIN users uc ON r.closed_by = uc.id
                          WHERE r.id = ?''', (review_id,)).fetchone()
    review['status_name'] = REVIEW_STATUS_NAME.get(review['status'], review['status'])
    return jsonify(review)

@app.route('/api/reviews/<int:review_id>/reject', methods=['POST'])
def reject_review(review_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    review = db.execute('SELECT * FROM review_items WHERE id = ?', (review_id,)).fetchone()
    if not review:
        return jsonify({'error': '复核项不存在'}), 404
    
    operator_id = data.get('operator_id')
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    if operator['role'] != 'RECEIVER':
        return jsonify({'error': '只有接收方才能退回复核项'}), 400
    
    if review['status'] not in ('PENDING_CLOSE', 'IN_PROGRESS', 'OPEN'):
        return jsonify({'error': f'当前状态（{REVIEW_STATUS_NAME.get(review["status"])}）不能退回'}), 400
    
    reason = data.get('reason', '').strip()
    if not reason:
        return jsonify({'error': '退回原因不能为空'}), 400
    
    old_status_name = REVIEW_STATUS_NAME.get(review['status'], review['status'])
    
    db.execute('''UPDATE review_items 
                 SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?''', (review_id,))
    
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (review['box_id'],)).fetchone()
    add_history(db, review['batch_id'], review['box_id'], '退回复核项', operator_id, operator['role'],
               box_no=box['box_no'] if box else None,
               reason=f'复核项#{review_id} 状态更新: {old_status_name} → 已退回 | 退回原因: {reason}')
    
    db.execute('''UPDATE reminders SET status = 'PENDING', process_note = NULL, processed_by = NULL, processed_at = NULL
                  WHERE review_id = ? AND status = 'PROCESSED' AND (merged_into IS NULL OR merged_into = 0)''', (review_id,))
    reverted_reminders = db.execute('''SELECT id FROM reminders 
                                       WHERE review_id = ? AND status = 'PENDING' AND (merged_into IS NULL OR merged_into = 0)''',
                                    (review_id,)).fetchall()
    for rv_rm in reverted_reminders:
        db.execute('''INSERT INTO reminder_logs (reminder_id, action, operator_id, detail)
                      VALUES (?, ?, ?, ?)''', (rv_rm['id'], '恢复催办', operator_id, '复核项退回，已处理催办恢复为待处理'))
    
    db.commit()
    
    review = db.execute('''SELECT r.*, bx.box_no, u.username as creator_name,
                          uc.username as closer_name
                          FROM review_items r
                          JOIN boxes bx ON r.box_id = bx.id
                          JOIN users u ON r.created_by = u.id
                          LEFT JOIN users uc ON r.closed_by = uc.id
                          WHERE r.id = ?''', (review_id,)).fetchone()
    review['status_name'] = REVIEW_STATUS_NAME.get(review['status'], review['status'])
    return jsonify(review)

@app.route('/api/reviews/<int:review_id>/close', methods=['POST'])
def close_review(review_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    review = db.execute('SELECT * FROM review_items WHERE id = ?', (review_id,)).fetchone()
    if not review:
        return jsonify({'error': '复核项不存在'}), 404
    
    operator_id = data.get('operator_id')
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    if operator['role'] != 'RECEIVER':
        return jsonify({'error': '只有接收方才能确认关闭复核项'}), 400
    
    if review['status'] != 'PENDING_CLOSE':
        return jsonify({'error': f'只有"申请关闭"状态才能确认关闭，当前状态：{REVIEW_STATUS_NAME.get(review["status"])}'}), 400
    
    old_status_name = REVIEW_STATUS_NAME.get(review['status'], review['status'])
    
    db.execute('''UPDATE review_items 
                 SET status = 'CLOSED', closed_by = ?, closed_at = CURRENT_TIMESTAMP, 
                     updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?''', (operator_id, review_id))
    
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (review['box_id'],)).fetchone()
    add_history(db, review['batch_id'], review['box_id'], '关闭复核项', operator_id, operator['role'],
               box_no=box['box_no'] if box else None,
               reason=f'复核项#{review_id} 状态更新: {old_status_name} → 已关闭')
    
    cancelled_reminders = db.execute('''SELECT id FROM reminders 
                                        WHERE review_id = ? AND status = 'PENDING' AND (merged_into IS NULL OR merged_into = 0)''',
                                     (review_id,)).fetchall()
    db.execute("UPDATE reminders SET status = 'CANCELLED' WHERE review_id = ? AND status = 'PENDING'", (review_id,))
    for cr in cancelled_reminders:
        db.execute('''INSERT INTO reminder_logs (reminder_id, action, operator_id, detail)
                      VALUES (?, ?, ?, ?)''', (cr['id'], '取消催办', operator_id, '复核项关闭，催办自动取消'))
    
    db.commit()
    
    review = db.execute('''SELECT r.*, bx.box_no, u.username as creator_name,
                          uc.username as closer_name
                          FROM review_items r
                          JOIN boxes bx ON r.box_id = bx.id
                          JOIN users u ON r.created_by = u.id
                          LEFT JOIN users uc ON r.closed_by = uc.id
                          WHERE r.id = ?''', (review_id,)).fetchone()
    review['status_name'] = REVIEW_STATUS_NAME.get(review['status'], review['status'])
    return jsonify(review)

@app.route('/api/reviews/<int:review_id>/reopen', methods=['POST'])
def reopen_review(review_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    review = db.execute('SELECT * FROM review_items WHERE id = ?', (review_id,)).fetchone()
    if not review:
        return jsonify({'error': '复核项不存在'}), 404
    
    operator_id = data.get('operator_id')
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    if operator['role'] != 'RECEIVER':
        return jsonify({'error': '只有接收方才能撤销重开已关闭的复核项'}), 400
    
    if review['status'] != 'CLOSED':
        return jsonify({'error': f'只有"已关闭"状态才能撤销重开，当前状态：{REVIEW_STATUS_NAME.get(review["status"])}'}), 400
    
    reason = data.get('reason', '').strip()
    if not reason:
        return jsonify({'error': '撤销重开原因不能为空'}), 400
    
    old_status_name = REVIEW_STATUS_NAME.get(review['status'], review['status'])
    
    db.execute('''UPDATE review_items 
                 SET status = 'OPEN', closed_by = NULL, closed_at = NULL,
                     updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?''', (review_id,))
    
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (review['box_id'],)).fetchone()
    add_history(db, review['batch_id'], review['box_id'], '撤销重开复核项', operator_id, operator['role'],
               box_no=box['box_no'] if box else None,
               reason=f'复核项#{review_id} 状态更新: {old_status_name} → 待处理 | 撤销原因: {reason}')
    
    db.execute('''UPDATE reminders SET status = 'PENDING', process_note = NULL, processed_by = NULL, processed_at = NULL
                  WHERE review_id = ? AND status = 'CANCELLED' AND (merged_into IS NULL OR merged_into = 0)''', (review_id,))
    restored_reminders = db.execute('''SELECT id FROM reminders 
                                       WHERE review_id = ? AND status = 'PENDING' AND (merged_into IS NULL OR merged_into = 0)
                                       AND created_at = (SELECT MAX(created_at) FROM reminders r2 WHERE r2.review_id = ?)''',
                                    (review_id, review_id)).fetchall()
    for rr in restored_reminders:
        db.execute('''INSERT INTO reminder_logs (reminder_id, action, operator_id, detail)
                      VALUES (?, ?, ?, ?)''', (rr['id'], '恢复催办', operator_id, '复核项重开，催办自动恢复'))
    
    db.commit()
    
    review = db.execute('''SELECT r.*, bx.box_no, u.username as creator_name,
                          uc.username as closer_name
                          FROM review_items r
                          JOIN boxes bx ON r.box_id = bx.id
                          JOIN users u ON r.created_by = u.id
                          LEFT JOIN users uc ON r.closed_by = uc.id
                          WHERE r.id = ?''', (review_id,)).fetchone()
    review['status_name'] = REVIEW_STATUS_NAME.get(review['status'], review['status'])
    return jsonify(review)

@app.route('/api/reviews/<int:review_id>/reminders', methods=['POST'])
def create_reminder(review_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    review = db.execute('SELECT * FROM review_items WHERE id = ?', (review_id,)).fetchone()
    if not review:
        return jsonify({'error': '复核项不存在'}), 404
    
    operator_id = data.get('created_by')
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    if operator['role'] != 'RECEIVER':
        return jsonify({'error': '只有接收方才能发起催办'}), 400
    
    if review['status'] not in ('OPEN', 'IN_PROGRESS', 'REJECTED'):
        return jsonify({'error': f'当前复核项状态（{REVIEW_STATUS_NAME.get(review["status"])}）不允许发起催办'}), 400
    
    reason = data.get('reason', '').strip()
    if not reason:
        return jsonify({'error': '催办原因不能为空'}), 400
    
    expected_completion = data.get('expected_completion')
    is_escalated = 1 if data.get('is_escalated', False) else 0
    urgency = data.get('urgency', 'NORMAL')
    if urgency not in URGENCY_ORDER:
        urgency = 'NORMAL'
    
    existing = db.execute('''SELECT * FROM reminders 
                             WHERE review_id = ? AND status = 'PENDING' AND merged_into IS NULL
                             LIMIT 1''', (review_id,)).fetchone()
    
    if existing:
        new_reason = existing['reason'] + '; ' + reason
        new_urgency = max_urgency(existing['urgency'], urgency)
        new_is_escalated = max(existing['is_escalated'], is_escalated)
        new_expected = existing['expected_completion']
        if expected_completion:
            if not new_expected or expected_completion > new_expected:
                new_expected = expected_completion
        
        db.execute('''UPDATE reminders SET reason = ?, expected_completion = ?, 
                      urgency = ?, is_escalated = ? WHERE id = ?''',
                   (new_reason, new_expected, new_urgency, new_is_escalated, existing['id']))
        
        db.execute('''INSERT INTO reminder_logs (reminder_id, action, operator_id, detail)
                      VALUES (?, ?, ?, ?)''',
                   (existing['id'], '合并催办', operator_id,
                    f'合并催办: 原因追加"{reason}", 紧急程度升级为{new_urgency}'))
        
        merged = db.execute('SELECT * FROM reminders WHERE id = ?', (existing['id'],)).fetchone()
        enrich_reminder(db, merged)
        db.commit()
        return jsonify(merged), 200
    
    cursor = db.execute('''INSERT INTO reminders 
                           (review_id, reason, expected_completion, is_escalated, urgency, status, created_by)
                           VALUES (?, ?, ?, ?, ?, 'PENDING', ?)''',
                        (review_id, reason, expected_completion, is_escalated, urgency, operator_id))
    reminder_id = cursor.lastrowid
    
    db.execute('''INSERT INTO reminder_logs (reminder_id, action, operator_id, detail)
                  VALUES (?, ?, ?, ?)''',
               (reminder_id, '发起催办', operator_id,
                f'发起催办: {reason}, 紧急程度: {URGENCY_LEVELS.get(urgency, urgency)}'
                + (f', 期望完成: {expected_completion}' if expected_completion else '')))
    
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (review['box_id'],)).fetchone()
    add_history(db, review['batch_id'], review['box_id'], '发起催办', operator_id, operator['role'],
               box_no=box['box_no'] if box else None,
               reason=f'复核项#{review_id} 催办: {reason}')
    
    db.commit()
    
    reminder = db.execute('SELECT * FROM reminders WHERE id = ?', (reminder_id,)).fetchone()
    enrich_reminder(db, reminder)
    return jsonify(reminder), 201

@app.route('/api/reviews/<int:review_id>/reminders', methods=['GET'])
def get_review_reminders(review_id):
    db = get_db()
    db.row_factory = dict_factory
    
    review = db.execute('SELECT * FROM review_items WHERE id = ?', (review_id,)).fetchone()
    if not review:
        return jsonify({'error': '复核项不存在'}), 404
    
    reminders = db.execute('''SELECT * FROM reminders 
                              WHERE review_id = ? AND (merged_into IS NULL OR merged_into = 0)
                              ORDER BY created_at DESC''', (review_id,)).fetchall()
    for rm in reminders:
        enrich_reminder(db, rm)
    return jsonify(reminders)

@app.route('/api/reminders/pending', methods=['GET'])
def get_pending_reminders():
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = request.args.get('operator_id', type=int)
    urgency_filter = request.args.get('urgency_filter')
    
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    query = '''SELECT rm.*, r.issue_type, r.issue_description, r.batch_id, r.status as review_status,
               bx.box_no
               FROM reminders rm
               JOIN review_items r ON rm.review_id = r.id
               JOIN boxes bx ON r.box_id = bx.id
               WHERE rm.status = 'PENDING' AND (rm.merged_into IS NULL OR rm.merged_into = 0)'''
    params = []
    
    if operator['role'] == 'RECEIVER':
        query += ' AND rm.created_by = ?'
        params.append(operator_id)
    
    if urgency_filter and urgency_filter in URGENCY_ORDER:
        query += ' AND rm.urgency = ?'
        params.append(urgency_filter)
    
    query += ' ORDER BY CASE rm.urgency WHEN \'CRITICAL\' THEN 0 WHEN \'URGENT\' THEN 1 ELSE 2 END, rm.created_at DESC'
    
    reminders = db.execute(query, params).fetchall()
    for rm in reminders:
        enrich_reminder(db, rm)
    
    grouped = {}
    for level in ['CRITICAL', 'URGENT', 'NORMAL']:
        items = [rm for rm in reminders if rm['urgency'] == level]
        if items:
            grouped[level] = items
    
    sorted_grouped = {}
    for level in ['CRITICAL', 'URGENT', 'NORMAL']:
        if level in grouped:
            sorted_grouped[level] = grouped[level]
    
    response = app.response_class(
        response=json.dumps(sorted_grouped, ensure_ascii=False),
        status=200,
        mimetype='application/json'
    )
    return response

@app.route('/api/reminders/<int:reminder_id>/process', methods=['POST'])
def process_reminder(reminder_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    reminder = db.execute('SELECT * FROM reminders WHERE id = ?', (reminder_id,)).fetchone()
    if not reminder:
        return jsonify({'error': '催办不存在'}), 404
    
    operator_id = data.get('operator_id')
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    if operator['role'] != 'SENDER':
        return jsonify({'error': '只有发送方才能处理催办'}), 400
    
    if reminder['status'] != 'PENDING':
        return jsonify({'error': f'当前催办状态（{REMINDER_STATUS.get(reminder["status"], reminder["status"])}）不允许处理'}), 400
    
    process_note = data.get('process_note', '').strip()
    if not process_note:
        return jsonify({'error': '处理备注不能为空'}), 400
    
    db.execute('''UPDATE reminders SET status = 'PROCESSED', processed_by = ?, 
                  processed_at = CURRENT_TIMESTAMP, process_note = ? WHERE id = ?''',
               (operator_id, process_note, reminder_id))
    
    db.execute('''INSERT INTO reminder_logs (reminder_id, action, operator_id, detail)
                  VALUES (?, ?, ?, ?)''',
               (reminder_id, '处理催办', operator_id, f'处理备注: {process_note}'))
    
    review = db.execute('SELECT * FROM review_items WHERE id = ?', (reminder['review_id'],)).fetchone()
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (review['box_id'],)).fetchone()
    add_history(db, review['batch_id'], review['box_id'], '处理催办', operator_id, operator['role'],
               box_no=box['box_no'] if box else None,
               reason=f'催办#{reminder_id} 处理备注: {process_note}')
    
    db.commit()
    
    reminder = db.execute('SELECT * FROM reminders WHERE id = ?', (reminder_id,)).fetchone()
    enrich_reminder(db, reminder)
    return jsonify(reminder)

@app.route('/api/reminders/<int:reminder_id>/revoke-escalation', methods=['POST'])
def revoke_escalation(reminder_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    reminder = db.execute('SELECT * FROM reminders WHERE id = ?', (reminder_id,)).fetchone()
    if not reminder:
        return jsonify({'error': '催办不存在'}), 404
    
    operator_id = data.get('operator_id')
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return jsonify({'error': '操作员不存在'}), 404
    
    if operator['role'] != 'RECEIVER':
        return jsonify({'error': '只有接收方才能撤销升级'}), 400
    
    if not reminder['is_escalated']:
        return jsonify({'error': '该催办未升级，无需撤销'}), 400
    
    new_urgency = reminder['urgency']
    if reminder['status'] == 'PENDING' and reminder['urgency'] == 'CRITICAL':
        new_urgency = 'URGENT'
    
    db.execute('''UPDATE reminders SET is_escalated = 0, urgency = ? WHERE id = ?''',
               (new_urgency, reminder_id))
    
    db.execute('''INSERT INTO reminder_logs (reminder_id, action, operator_id, detail)
                  VALUES (?, ?, ?, ?)''',
               (reminder_id, '撤销升级', operator_id,
                f'撤销升级, 紧急程度调整为{URGENCY_LEVELS.get(new_urgency, new_urgency)}'))
    
    review = db.execute('SELECT * FROM review_items WHERE id = ?', (reminder['review_id'],)).fetchone()
    box = db.execute('SELECT * FROM boxes WHERE id = ?', (review['box_id'],)).fetchone()
    add_history(db, review['batch_id'], review['box_id'], '撤销升级', operator_id, operator['role'],
               box_no=box['box_no'] if box else None,
               reason=f'催办#{reminder_id} 撤销升级')
    
    db.commit()
    
    reminder = db.execute('SELECT * FROM reminders WHERE id = ?', (reminder_id,)).fetchone()
    enrich_reminder(db, reminder)
    return jsonify(reminder)

@app.route('/api/reminders/stats', methods=['GET'])
def get_reminder_stats():
    db = get_db()
    db.row_factory = dict_factory
    
    total_pending = db.execute('''SELECT COUNT(*) as cnt FROM reminders 
                                  WHERE status = 'PENDING' AND (merged_into IS NULL OR merged_into = 0)''').fetchone()['cnt']
    
    by_urgency = {}
    for level in URGENCY_ORDER:
        cnt = db.execute('''SELECT COUNT(*) as cnt FROM reminders 
                            WHERE status = 'PENDING' AND urgency = ? AND (merged_into IS NULL OR merged_into = 0)''',
                         (level,)).fetchone()['cnt']
        by_urgency[level] = cnt
    
    processed_today = db.execute('''SELECT COUNT(*) as cnt FROM reminders 
                                    WHERE status = 'PROCESSED' AND DATE(processed_at) = DATE('now')''').fetchone()['cnt']
    
    return jsonify({
        'total_pending': total_pending,
        'by_urgency': by_urgency,
        'processed_today': processed_today
    })

if __name__ == '__main__':
    init_db()
    app.run(host='127.0.0.1', port=5000, debug=True)
