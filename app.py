from flask import Flask, request, jsonify, g, send_file
from flask_cors import CORS
import sqlite3
import json
import csv
import io
import os
import time
import socket
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

REMINDER_MERGE_WINDOW_SECONDS = 60

REMINDER_STATUS = {
    'PENDING': '待处理',
    'PROCESSED': '已处理',
    'MERGED': '已合并',
    'CANCELLED': '已取消'
}

STRATEGY_STATUS = {
    'DRAFT': '草稿',
    'ACTIVE': '已启用',
    'INACTIVE': '已停用'
}

STRATEGY_STATUS_NAME = {
    'DRAFT': '草稿',
    'ACTIVE': '已启用',
    'INACTIVE': '已停用'
}

STRATEGY_ACTION = {
    'CREATE': '创建策略',
    'UPDATE': '更新策略',
    'ENABLE': '启用策略',
    'DISABLE': '停用策略',
    'ROLLBACK': '回滚策略',
    'IMPORT': '导入策略',
    'EXPORT': '导出策略',
    'UNDO': '撤销变更'
}

STRATEGY_PERMISSION_ROLES = ['RECEIVER']

CONNECTION_STATUS = {
    'UNKNOWN': '未探测',
    'AVAILABLE': '可用',
    'UNAVAILABLE': '不可用',
    'TIMEOUT': '超时',
    'ERROR': '错误'
}

CONNECTION_STATUS_NAME = {
    'UNKNOWN': '未探测',
    'AVAILABLE': '可用',
    'UNAVAILABLE': '不可用',
    'TIMEOUT': '超时',
    'ERROR': '错误'
}

CONNECTION_LOG_ACTION = {
    'CREATE_CONFIG': '创建连接配置',
    'UPDATE_CONFIG': '更新连接配置',
    'DELETE_CONFIG': '删除连接配置',
    'PROBE_CONNECTION': '探测连接',
    'OPEN_ENTRY': '打开入口',
    'SWITCH_OPERATOR': '切换操作人',
    'IMPORT_CONFIG': '导入连接配置',
    'EXPORT_CONFIG': '导出连接配置',
    'ROLLBACK_CONFIG': '回退配置',
    'RESOLVE_CONFLICT': '解决配置冲突',
    'PUBLISH_CONFIG': '发布配置'
}

CONNECTION_CONFLICT_RESOLUTION = {
    'OVERWRITE': '覆盖本地',
    'KEEP_LOCAL': '保留本地',
    'SAVE_AS_NEW': '另存为新配置'
}

CONNECTION_PUBLISH_PERMISSION_ROLES = ['RECEIVER']

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
                'processed_by', 'processed_at', 'process_note', 'merged_into', 'updated_at']:
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
    
    c.execute('''CREATE TABLE IF NOT EXISTS reminder_strategies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    trigger_conditions TEXT NOT NULL,
                    escalation_order TEXT NOT NULL,
                    cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                    timeout_hours INTEGER NOT NULL DEFAULT 24,
                    notify_targets TEXT NOT NULL,
                    scope_filter TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by INTEGER,
                    updated_at TIMESTAMP,
                    FOREIGN KEY (created_by) REFERENCES users(id),
                    FOREIGN KEY (updated_by) REFERENCES users(id))''')
    
    for col in ['priority', 'updated_by', 'updated_at']:
        try:
            c.execute(f'ALTER TABLE reminder_strategies ADD COLUMN {col}')
        except sqlite3.OperationalError:
            pass
    
    c.execute('''CREATE TABLE IF NOT EXISTS reminder_strategy_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    operator_id INTEGER NOT NULL,
                    detail TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (strategy_id) REFERENCES reminder_strategies(id),
                    FOREIGN KEY (operator_id) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS reminder_strategy_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id INTEGER NOT NULL,
                    snapshot_data TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (strategy_id) REFERENCES reminder_strategies(id),
                    FOREIGN KEY (created_by) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS reminder_strategy_applied (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id INTEGER NOT NULL,
                    review_id INTEGER NOT NULL,
                    escalation_level INTEGER NOT NULL DEFAULT 0,
                    last_reminded_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (strategy_id) REFERENCES reminder_strategies(id),
                    FOREIGN KEY (review_id) REFERENCES review_items(id),
                    UNIQUE(strategy_id, review_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS connection_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_name TEXT UNIQUE NOT NULL,
                    service_host TEXT NOT NULL DEFAULT '127.0.0.1',
                    service_port INTEGER NOT NULL DEFAULT 5002,
                    entry_path TEXT NOT NULL DEFAULT '/',
                    protocol TEXT NOT NULL DEFAULT 'http',
                    current_operator_id INTEGER,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    is_published INTEGER NOT NULL DEFAULT 0,
                    published_by INTEGER,
                    published_at TIMESTAMP,
                    last_probe_status TEXT DEFAULT 'UNKNOWN',
                    last_probe_at TIMESTAMP,
                    last_probe_message TEXT,
                    last_available_at TIMESTAMP,
                    config_version INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (current_operator_id) REFERENCES users(id),
                    FOREIGN KEY (created_by) REFERENCES users(id),
                    FOREIGN KEY (updated_by) REFERENCES users(id),
                    FOREIGN KEY (published_by) REFERENCES users(id))''')
    
    for col in ['is_published', 'published_by', 'published_at', 'config_version']:
        try:
            c.execute(f'ALTER TABLE connection_configs ADD COLUMN {col}')
        except sqlite3.OperationalError:
            pass
    
    c.execute('''CREATE TABLE IF NOT EXISTS connection_config_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_id INTEGER NOT NULL,
                    snapshot_data TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (config_id) REFERENCES connection_configs(id),
                    FOREIGN KEY (created_by) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS connection_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_id INTEGER,
                    profile_name TEXT,
                    action TEXT NOT NULL,
                    operator_id INTEGER,
                    operator_name TEXT,
                    detail TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (config_id) REFERENCES connection_configs(id),
                    FOREIGN KEY (operator_id) REFERENCES users(id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS connection_diagnostics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_id INTEGER NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    diagnostic_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    suggestion TEXT,
                    recovery_action TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (config_id) REFERENCES connection_configs(id))''')
    
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (username, role) VALUES (?, ?)", ('sender', 'SENDER'))
        c.execute("INSERT INTO users (username, role) VALUES (?, ?)", ('receiver', 'RECEIVER'))
    
    c.execute("SELECT COUNT(*) FROM connection_configs")
    if c.fetchone()[0] == 0:
        c.execute('''INSERT INTO connection_configs 
                     (profile_name, service_host, service_port, entry_path, protocol, 
                      is_default, config_version)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  ('默认连接', '127.0.0.1', 5002, '/', 'http', 1, 1))
    
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
        if rv.get('deadline') and rv['status'] != 'CLOSED':
            ov_result = db.execute("SELECT DATE(?) < DATE('now') as is_ov", (rv['deadline'],)).fetchone()
            rv['is_overdue'] = 1 if ov_result and ov_result['is_ov'] else 0
        else:
            rv['is_overdue'] = 0
        reminder_stats = db.execute('''SELECT COUNT(*) as total,
            SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN is_escalated = 1 AND status = 'PENDING' THEN 1 ELSE 0 END) as escalated_pending,
            SUM(CASE WHEN status = 'PROCESSED' THEN 1 ELSE 0 END) as processed,
            SUM(CASE WHEN status = 'CANCELLED' THEN 1 ELSE 0 END) as cancelled
            FROM reminders WHERE review_id = ? AND (merged_into IS NULL OR merged_into = 0)''', (rv['id'],)).fetchone()
        rv['reminder_total'] = reminder_stats['total']
        rv['reminder_pending'] = reminder_stats['pending']
        rv['reminder_escalated'] = reminder_stats['escalated_pending']
        rv['reminder_processed'] = reminder_stats['processed']
        rv['reminder_cancelled'] = reminder_stats['cancelled']
        
        last_reminder = db.execute('''SELECT r.*, u.username as creator_name
            FROM reminders r JOIN users u ON r.created_by = u.id
            WHERE r.review_id = ? AND r.status != 'MERGED' AND (r.merged_into IS NULL OR r.merged_into = 0)
            ORDER BY r.created_at DESC LIMIT 1''', (rv['id'],)).fetchone()
        if last_reminder:
            rv['reminder_latest_creator'] = last_reminder['creator_name']
            rv['reminder_latest_at'] = last_reminder['created_at']
            rv['reminder_latest_urgency'] = last_reminder['urgency']
            rv['reminder_latest_urgency_name'] = URGENCY_LEVELS.get(last_reminder['urgency'], last_reminder['urgency'])
            rv['reminder_latest_is_escalated'] = last_reminder['is_escalated']
            rv['reminder_latest_status'] = last_reminder['status']
            rv['reminder_latest_status_name'] = REMINDER_STATUS.get(last_reminder['status'], last_reminder['status'])
            if last_reminder.get('process_note'):
                rv['reminder_latest_process_note'] = last_reminder['process_note']
            if last_reminder.get('processed_by'):
                proc_user = db.execute('SELECT username FROM users WHERE id = ?', (last_reminder['processed_by'],)).fetchone()
                rv['reminder_latest_processor'] = proc_user['username'] if proc_user else None
                rv['reminder_latest_processed_at'] = last_reminder['processed_at']
        else:
            rv['reminder_latest_creator'] = None
            rv['reminder_latest_at'] = None
            rv['reminder_latest_urgency'] = None
            rv['reminder_latest_urgency_name'] = None
            rv['reminder_latest_is_escalated'] = 0
            rv['reminder_latest_status'] = None
            rv['reminder_latest_status_name'] = None
            rv['reminder_latest_process_note'] = None
            rv['reminder_latest_processor'] = None
            rv['reminder_latest_processed_at'] = None
        
        reminder_urgency_stats = db.execute('''SELECT urgency, COUNT(*) as cnt
            FROM reminders WHERE review_id = ? AND status = 'PENDING' AND (merged_into IS NULL OR merged_into = 0)
            GROUP BY urgency''', (rv['id'],)).fetchall()
        rv['reminder_by_urgency'] = {level: 0 for level in URGENCY_ORDER}
        for row in reminder_urgency_stats:
            rv['reminder_by_urgency'][row['urgency']] = row['cnt']
    
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
    overdue_reviews = len([r for r in reviews if r.get('deadline') and r['status'] != 'CLOSED' 
                           and datetime.strptime(r['deadline'], '%Y-%m-%d').date() < datetime.now().date()])
    
    pending_reminders = len([r for r in all_reminders if r['status'] == 'PENDING'])
    processed_reminders = len([r for r in all_reminders if r['status'] == 'PROCESSED'])
    cancelled_reminders = len([r for r in all_reminders if r['status'] == 'CANCELLED'])
    escalated_pending = len([r for r in all_reminders if r['status'] == 'PENDING' and r['is_escalated']])
    critical_pending = len([r for r in all_reminders if r['status'] == 'PENDING' and r['urgency'] == 'CRITICAL'])
    urgent_pending = len([r for r in all_reminders if r['status'] == 'PENDING' and r['urgency'] == 'URGENT'])
    normal_pending = len([r for r in all_reminders if r['status'] == 'PENDING' and r['urgency'] == 'NORMAL'])
    overdue_pending = len([r for r in all_reminders if r['status'] == 'PENDING' 
                           and any(rv.get('deadline') and rv['status'] != 'CLOSED' 
                                   and datetime.strptime(rv['deadline'], '%Y-%m-%d').date() < datetime.now().date()
                                   for rv in reviews if rv['id'] == r['review_id'])])
    
    writer.writerow([f'复核项统计: 共{total_reviews}项, 待处理{open_reviews}项, 已关闭{closed_reviews}项, 超期{overdue_reviews}项'])
    writer.writerow([f'催办统计: 共{total_reminders}条, 待处理{pending_reminders}条, 已处理{processed_reminders}条, 已取消{cancelled_reminders}条'])
    writer.writerow([f'催办分级: 特急{critical_pending}条, 紧急{urgent_pending}条, 普通{normal_pending}条, 已升级{escalated_pending}条, 超期{overdue_pending}条'])
    
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
        writer.writerow(['复核ID', '盒号', '问题类型', '问题描述', '责任方', '截止时间', '是否超期', '处理说明', '状态', '催办次数', '最近催办人', '最近催办时间', '最近催办紧急程度', '提报人', '提报时间', '关闭人', '关闭时间'])
        for r in reviews:
            is_overdue = '否'
            if r.get('deadline') and r['status'] != 'CLOSED':
                try:
                    if datetime.strptime(r['deadline'], '%Y-%m-%d').date() < datetime.now().date():
                        is_overdue = '是'
                except:
                    pass
            
            r_reminders = [rm for rm in all_reminders if rm['review_id'] == r['id']]
            reminder_count = len(r_reminders)
            last_reminder = max(r_reminders, key=lambda x: x['created_at']) if r_reminders else None
            
            writer.writerow([
                r['id'],
                r['box_no'],
                r['issue_type'],
                r['issue_description'],
                r['responsible_party'] or '',
                r['deadline'] or '',
                is_overdue,
                r['handling_note'] or '',
                REVIEW_STATUS_NAME.get(r['status'], r['status']),
                reminder_count,
                last_reminder['creator_name'] if last_reminder else '',
                last_reminder['created_at'] if last_reminder else '',
                URGENCY_LEVELS.get(last_reminder['urgency'], last_reminder['urgency']) if last_reminder else '',
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
        if rv.get('deadline') and rv['status'] != 'CLOSED':
            ov_result = db.execute("SELECT DATE(?) < DATE('now') as is_ov", (rv['deadline'],)).fetchone()
            rv['is_overdue'] = 1 if ov_result and ov_result['is_ov'] else 0
        else:
            rv['is_overdue'] = 0
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
                            ORDER BY id DESC LIMIT 1''',
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
    
    operator_id = data.get('operator_id')
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
    
    existing_pending = db.execute('''SELECT * FROM reminders 
                                      WHERE review_id = ? AND status = 'PENDING' AND merged_into IS NULL
                                      ORDER BY id DESC LIMIT 1''', (review_id,)).fetchone()
    
    if existing_pending:
        time_diff = db.execute("SELECT (julianday('now') - julianday(?)) * 86400 as diff",
                               (existing_pending['created_at'],)).fetchone()['diff']
        
        if time_diff is not None and time_diff < REMINDER_MERGE_WINDOW_SECONDS and existing_pending['created_by'] == operator_id:
            db.rollback() if False else None
            return jsonify({
                'error': f'操作太频繁，请在{REMINDER_MERGE_WINDOW_SECONDS}秒后再试，或使用已有催办记录',
                'existing_reminder_id': existing_pending['id']
            }), 429
        
        new_reason = existing_pending['reason'] + '; ' + reason
        new_urgency = max_urgency(existing_pending['urgency'], urgency)
        new_is_escalated = max(existing_pending['is_escalated'], is_escalated)
        new_expected = existing_pending['expected_completion']
        if expected_completion:
            if not new_expected or expected_completion > new_expected:
                new_expected = expected_completion
        
        db.execute('''UPDATE reminders SET reason = ?, expected_completion = ?, 
                      urgency = ?, is_escalated = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?''',
                   (new_reason, new_expected, new_urgency, new_is_escalated, existing_pending['id']))
        
        db.execute('''INSERT INTO reminder_logs (reminder_id, action, operator_id, detail)
                      VALUES (?, ?, ?, ?)''',
                   (existing_pending['id'], '合并催办', operator_id,
                    f'合并催办: 原因追加"{reason}", 紧急程度升级为{URGENCY_LEVELS.get(new_urgency, new_urgency)}'))
        
        merged = db.execute('SELECT * FROM reminders WHERE id = ?', (existing_pending['id'],)).fetchone()
        enrich_reminder(db, merged)
        merged['merged'] = True
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
                              ORDER BY id DESC''', (review_id,)).fetchall()
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
               r.deadline, r.responsible_party,
               bx.box_no, b.batch_no
               FROM reminders rm
               JOIN review_items r ON rm.review_id = r.id
               JOIN boxes bx ON r.box_id = bx.id
               JOIN batches b ON r.batch_id = b.id
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
        if rm.get('deadline') and rm.get('review_status') != 'CLOSED':
            ov_result = db.execute("SELECT DATE(?) < DATE('now') as is_ov", (rm['deadline'],)).fetchone()
            rm['is_overdue'] = 1 if ov_result and ov_result['is_ov'] else 0
        else:
            rm['is_overdue'] = 0
        rm['review_status_name'] = REVIEW_STATUS_NAME.get(rm.get('review_status', ''), rm.get('review_status', ''))
    
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
    
    overdue_pending = db.execute('''SELECT COUNT(*) as cnt FROM reminders rm
        JOIN review_items r ON rm.review_id = r.id
        WHERE rm.status = 'PENDING' AND (rm.merged_into IS NULL OR rm.merged_into = 0)
        AND r.status != 'CLOSED' AND r.deadline IS NOT NULL AND DATE(r.deadline) < DATE('now')
    ''').fetchone()['cnt']
    
    escalated_pending = db.execute('''SELECT COUNT(*) as cnt FROM reminders 
        WHERE status = 'PENDING' AND is_escalated = 1 AND (merged_into IS NULL OR merged_into = 0)''').fetchone()['cnt']
    
    return jsonify({
        'total_pending': total_pending,
        'by_urgency': by_urgency,
        'processed_today': processed_today,
        'overdue_pending': overdue_pending,
        'escalated_pending': escalated_pending
    })

def check_strategy_permission(operator_id, db):
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return False, '操作员不存在'
    if operator['role'] not in STRATEGY_PERMISSION_ROLES:
        return False, f'只有{",".join(STRATEGY_PERMISSION_ROLES)}角色才能操作催办策略'
    return True, None

def enrich_strategy(db, strategy):
    strategy['status_name'] = STRATEGY_STATUS_NAME.get(strategy['status'], strategy['status'])
    creator = db.execute('SELECT username FROM users WHERE id = ?', (strategy['created_by'],)).fetchone()
    strategy['creator_name'] = creator['username'] if creator else None
    if strategy.get('updated_by'):
        updator = db.execute('SELECT username FROM users WHERE id = ?', (strategy['updated_by'],)).fetchone()
        strategy['updator_name'] = updator['username'] if updator else None
    else:
        strategy['updator_name'] = None
    try:
        strategy['trigger_conditions'] = json.loads(strategy['trigger_conditions']) if strategy.get('trigger_conditions') else {}
        strategy['escalation_order'] = json.loads(strategy['escalation_order']) if strategy.get('escalation_order') else []
        strategy['notify_targets'] = json.loads(strategy['notify_targets']) if strategy.get('notify_targets') else []
        strategy['scope_filter'] = json.loads(strategy['scope_filter']) if strategy.get('scope_filter') else {}
    except Exception:
        pass
    return strategy

def add_strategy_log(db, strategy_id, action, operator_id, detail=None):
    db.execute('''INSERT INTO reminder_strategy_logs 
                  (strategy_id, action, operator_id, detail)
                  VALUES (?, ?, ?, ?)''',
               (strategy_id, action, operator_id, json.dumps(detail, ensure_ascii=False) if detail else None))

def save_strategy_snapshot(db, strategy_id, operator_id):
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    if not strategy:
        return
    snapshot_data = {
        'name': strategy['name'],
        'description': strategy['description'],
        'trigger_conditions': strategy['trigger_conditions'],
        'escalation_order': strategy['escalation_order'],
        'cooldown_minutes': strategy['cooldown_minutes'],
        'timeout_hours': strategy['timeout_hours'],
        'notify_targets': strategy['notify_targets'],
        'scope_filter': strategy['scope_filter'],
        'priority': strategy['priority'],
        'status': strategy['status'],
        'version': strategy['version']
    }
    db.execute('''INSERT INTO reminder_strategy_snapshots 
                  (strategy_id, snapshot_data, version, created_by)
                  VALUES (?, ?, ?, ?)''',
               (strategy_id, json.dumps(snapshot_data, ensure_ascii=False), strategy['version'], operator_id))

def get_reviews_for_strategy(db, strategy):
    scope = strategy['scope_filter'] if isinstance(strategy['scope_filter'], dict) else json.loads(strategy['scope_filter'])
    trigger = strategy['trigger_conditions'] if isinstance(strategy['trigger_conditions'], dict) else json.loads(strategy['trigger_conditions'])
    
    query = '''SELECT r.*, bx.box_no, b.batch_no, u.username as creator_name
               FROM review_items r
               JOIN boxes bx ON r.box_id = bx.id
               JOIN batches b ON r.batch_id = b.id
               JOIN users u ON r.created_by = u.id
               WHERE r.status != 'CLOSED' '''
    params = []
    
    if scope.get('batch_ids'):
        placeholders = ','.join(['?'] * len(scope['batch_ids']))
        query += f' AND r.batch_id IN ({placeholders})'
        params.extend(scope['batch_ids'])
    
    if scope.get('box_ids'):
        placeholders = ','.join(['?'] * len(scope['box_ids']))
        query += f' AND r.box_id IN ({placeholders})'
        params.extend(scope['box_ids'])
    
    if trigger.get('issue_types'):
        placeholders = ','.join(['?'] * len(trigger['issue_types']))
        query += f' AND r.issue_type IN ({placeholders})'
        params.extend(trigger['issue_types'])
    
    if trigger.get('statuses'):
        placeholders = ','.join(['?'] * len(trigger['statuses']))
        query += f' AND r.status IN ({placeholders})'
        params.extend(trigger['statuses'])
    
    if trigger.get('min_hours_open'):
        query += f" AND (julianday('now') - julianday(r.created_at)) * 24 >= ?"
        params.append(trigger['min_hours_open'])
    
    if trigger.get('is_overdue'):
        query += " AND r.deadline IS NOT NULL AND DATE(r.deadline) < DATE('now')"
    
    if trigger.get('has_pending_reminder') == False:
        query += " AND NOT EXISTS (SELECT 1 FROM reminders rm WHERE rm.review_id = r.id AND rm.status = 'PENDING')"
    elif trigger.get('has_pending_reminder') == True:
        query += " AND EXISTS (SELECT 1 FROM reminders rm WHERE rm.review_id = r.id AND rm.status = 'PENDING')"
    
    query += ' ORDER BY r.created_at DESC'
    
    return db.execute(query, params).fetchall()

def check_conflict_and_resolve(db, reviews, strategies, include_match_details=True):
    results = []
    for review in reviews:
        matched_strategies = []
        unmatched_strategies = []
        
        for strategy in strategies:
            if include_match_details:
                match_detail = review_matches_strategy(review, strategy, return_details=True)
                strategy_dict = dict(strategy)
                strategy_dict['match_details'] = match_detail
                if match_detail['all_matched']:
                    matched_strategies.append(strategy_dict)
                else:
                    unmatched_strategies.append(strategy_dict)
            else:
                if review_matches_strategy(review, strategy):
                    matched_strategies.append(dict(strategy))
        
        if len(matched_strategies) == 0:
            continue
        
        sorted_matched = sorted(matched_strategies, key=lambda s: (-s['priority'], s['id']))
        selected = sorted_matched[0]
        
        not_selected_high_priority = []
        for s in strategies:
            s_dict = dict(s)
            if s_dict['id'] != selected['id'] and s_dict['priority'] >= selected['priority']:
                if include_match_details:
                    match_detail = review_matches_strategy(review, s, return_details=True)
                    s_dict['match_details'] = match_detail
                    if not match_detail['all_matched']:
                        not_selected_high_priority.append({
                            'strategy': s_dict,
                            'reason': f"优先级({s_dict['priority']}) ≥ 选中策略优先级({selected['priority']})，但未满足触发条件: {'; '.join(match_detail['failed_reasons']) if match_detail['failed_reasons'] else '未知原因'}"
                        })
                else:
                    if not review_matches_strategy(review, s):
                        not_selected_high_priority.append({
                            'strategy': s_dict,
                            'reason': f"优先级({s_dict['priority']}) ≥ 选中策略优先级({selected['priority']})，但未满足触发条件"
                        })
        
        conflict = len(matched_strategies) > 1
        
        hit_reasons = []
        if include_match_details and selected.get('match_details'):
            md = selected['match_details']
            for check in md['scope_checks'] + md['trigger_checks']:
                if check['matched']:
                    hit_reasons.append(f"{check['condition']}: 期望值{check['expected']}, 实际值{check['actual']}，匹配成功")
        
        resolution_detail = ''
        if conflict:
            resolution_detail = f'存在{len(matched_strategies)}个匹配策略，按优先级排序: '
            resolution_detail += ' → '.join([f'{s["name"]}(优先级={s["priority"]})' for s in sorted_matched])
        else:
            resolution_detail = '唯一匹配，无冲突'
        
        results.append({
            'review_id': review['id'],
            'review': dict(review),
            'matched_strategies': matched_strategies,
            'unmatched_strategies': unmatched_strategies if include_match_details else [],
            'not_selected_high_priority': not_selected_high_priority,
            'conflict': conflict,
            'selected_strategy': selected,
            'hit_reasons': hit_reasons,
            'resolution': f'冲突裁决：选择优先级最高的策略"{selected["name"]}"(优先级={selected["priority"]})' if conflict else '唯一匹配',
            'resolution_detail': resolution_detail
        })
    return results

def review_matches_strategy(review, strategy, return_details=False):
    scope = strategy['scope_filter'] if isinstance(strategy['scope_filter'], dict) else json.loads(strategy['scope_filter'])
    trigger = strategy['trigger_conditions'] if isinstance(strategy['trigger_conditions'], dict) else json.loads(strategy['trigger_conditions'])
    
    match_details = {
        'scope_checks': [],
        'trigger_checks': [],
        'all_matched': True,
        'failed_reasons': []
    }
    
    if scope.get('batch_ids'):
        matched = review['batch_id'] in scope['batch_ids']
        match_details['scope_checks'].append({
            'condition': 'batch_ids',
            'expected': scope['batch_ids'],
            'actual': review['batch_id'],
            'matched': matched
        })
        if not matched:
            match_details['all_matched'] = False
            match_details['failed_reasons'].append(f"批次ID不在适用范围内: 期望{scope['batch_ids']}, 实际{review['batch_id']}")
    
    if scope.get('box_ids'):
        matched = review['box_id'] in scope['box_ids']
        match_details['scope_checks'].append({
            'condition': 'box_ids',
            'expected': scope['box_ids'],
            'actual': review['box_id'],
            'matched': matched
        })
        if not matched:
            match_details['all_matched'] = False
            match_details['failed_reasons'].append(f"盒子ID不在适用范围内: 期望{scope['box_ids']}, 实际{review['box_id']}")
    
    if trigger.get('issue_types'):
        matched = review['issue_type'] in trigger['issue_types']
        match_details['trigger_checks'].append({
            'condition': 'issue_types',
            'expected': trigger['issue_types'],
            'actual': review['issue_type'],
            'matched': matched
        })
        if not matched:
            match_details['all_matched'] = False
            match_details['failed_reasons'].append(f"问题类型不匹配: 期望{trigger['issue_types']}, 实际{review['issue_type']}")
    
    if trigger.get('statuses'):
        matched = review['status'] in trigger['statuses']
        match_details['trigger_checks'].append({
            'condition': 'statuses',
            'expected': trigger['statuses'],
            'actual': review['status'],
            'matched': matched
        })
        if not matched:
            match_details['all_matched'] = False
            match_details['failed_reasons'].append(f"状态不匹配: 期望{trigger['statuses']}, 实际{review['status']}")
    
    if trigger.get('min_hours_open') and trigger['min_hours_open'] > 0:
        hours_open = None
        try:
            from datetime import datetime
            created_at = datetime.fromisoformat(review['created_at'].replace('Z', '+00:00') if 'Z' in review['created_at'] else review['created_at'])
            hours_open = (datetime.now() - created_at).total_seconds() / 3600
        except Exception:
            hours_open = 0
        matched = hours_open >= trigger['min_hours_open']
        match_details['trigger_checks'].append({
            'condition': 'min_hours_open',
            'expected': trigger['min_hours_open'],
            'actual': round(hours_open, 2),
            'matched': matched
        })
        if not matched:
            match_details['all_matched'] = False
            match_details['failed_reasons'].append(f"开放时长不足: 期望≥{trigger['min_hours_open']}小时, 实际{round(hours_open, 2)}小时")
    
    if trigger.get('is_overdue') is not None:
        is_overdue = False
        if review.get('deadline'):
            try:
                from datetime import datetime
                deadline = datetime.fromisoformat(review['deadline'].replace('Z', '+00:00') if 'Z' in review['deadline'] else review['deadline'])
                is_overdue = deadline < datetime.now()
            except Exception:
                is_overdue = False
        matched = is_overdue == trigger['is_overdue']
        match_details['trigger_checks'].append({
            'condition': 'is_overdue',
            'expected': trigger['is_overdue'],
            'actual': is_overdue,
            'matched': matched
        })
        if not matched:
            match_details['all_matched'] = False
            match_details['failed_reasons'].append(f"超期状态不匹配: 期望{trigger['is_overdue']}, 实际{is_overdue}")
    
    if trigger.get('has_pending_reminder') is not None:
        has_pending = review.get('reminder_pending', 0) > 0
        matched = has_pending == trigger['has_pending_reminder']
        match_details['trigger_checks'].append({
            'condition': 'has_pending_reminder',
            'expected': trigger['has_pending_reminder'],
            'actual': has_pending,
            'matched': matched
        })
        if not matched:
            match_details['all_matched'] = False
            match_details['failed_reasons'].append(f"待处理催办状态不匹配: 期望{trigger['has_pending_reminder']}, 实际{has_pending}")
    
    if return_details:
        return match_details
    return match_details['all_matched']

def calculate_escalation_level(db, strategy, review):
    applied = db.execute('''SELECT * FROM reminder_strategy_applied 
                            WHERE strategy_id = ? AND review_id = ?''',
                         (strategy['id'], review['id'])).fetchone()
    
    escalation_order = strategy['escalation_order'] if isinstance(strategy['escalation_order'], list) else json.loads(strategy['escalation_order'])
    
    if not applied:
        return 0, escalation_order[0] if escalation_order else None
    
    current_level = applied['escalation_level']
    timeout_hours = strategy['timeout_hours']
    
    last_reminder = db.execute('''SELECT MAX(created_at) as last_at FROM reminders 
                                  WHERE review_id = ?''', (review['id'],)).fetchone()
    
    if last_reminder and last_reminder['last_at']:
        hours_since = db.execute("SELECT (julianday('now') - julianday(?)) * 24 as hours",
                                 (last_reminder['last_at'],)).fetchone()['hours']
        if hours_since and hours_since >= timeout_hours:
            next_level = min(current_level + 1, len(escalation_order) - 1)
            return next_level, escalation_order[next_level] if next_level < len(escalation_order) else None
    
    return current_level, escalation_order[current_level] if current_level < len(escalation_order) else None

def check_cooldown(db, strategy, review):
    applied = db.execute('''SELECT * FROM reminder_strategy_applied 
                            WHERE strategy_id = ? AND review_id = ?''',
                         (strategy['id'], review['id'])).fetchone()
    
    if not applied or not applied['last_reminded_at']:
        return False, None
    
    cooldown_minutes = strategy['cooldown_minutes']
    minutes_since = db.execute("SELECT (julianday('now') - julianday(?)) * 1440 as minutes",
                               (applied['last_reminded_at'],)).fetchone()['minutes']
    
    if minutes_since and minutes_since < cooldown_minutes:
        return True, cooldown_minutes - minutes_since
    
    return False, None

@app.route('/api/strategies', methods=['GET'])
def get_strategies():
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = request.args.get('operator_id', type=int)
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    status_filter = request.args.get('status')
    query = 'SELECT * FROM reminder_strategies'
    params = []
    if status_filter:
        query += ' WHERE status = ?'
        params.append(status_filter)
    query += ' ORDER BY priority DESC, created_at DESC'
    
    strategies = db.execute(query, params).fetchall()
    for s in strategies:
        enrich_strategy(db, s)
    return jsonify(strategies)

@app.route('/api/strategies/<int:strategy_id>', methods=['GET'])
def get_strategy_detail(strategy_id):
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = request.args.get('operator_id', type=int)
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    if not strategy:
        return jsonify({'error': '策略不存在'}), 404
    
    enrich_strategy(db, strategy)
    
    logs = db.execute('''SELECT l.*, u.username as operator_name
                         FROM reminder_strategy_logs l JOIN users u ON l.operator_id = u.id
                         WHERE l.strategy_id = ? ORDER BY l.created_at DESC''',
                      (strategy_id,)).fetchall()
    for log in logs:
        if log.get('detail'):
            try:
                log['detail'] = json.loads(log['detail'])
            except Exception:
                pass
    
    snapshots = db.execute('''SELECT s.*, u.username as creator_name
                              FROM reminder_strategy_snapshots s JOIN users u ON s.created_by = u.id
                              WHERE s.strategy_id = ? ORDER BY s.created_at DESC''',
                           (strategy_id,)).fetchall()
    for snap in snapshots:
        if snap.get('snapshot_data'):
            try:
                snap['snapshot_data'] = json.loads(snap['snapshot_data'])
            except Exception:
                pass
    
    return jsonify({
        'strategy': strategy,
        'logs': logs,
        'snapshots': snapshots
    })

@app.route('/api/strategies', methods=['POST'])
def create_strategy():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = data.get('operator_id')
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '策略名称不能为空'}), 400
    
    existing = db.execute('SELECT * FROM reminder_strategies WHERE name = ?', (name,)).fetchone()
    if existing:
        return jsonify({'error': '策略名称已存在'}), 409
    
    try:
        trigger_conditions = json.dumps(data.get('trigger_conditions', {}), ensure_ascii=False)
        escalation_order = json.dumps(data.get('escalation_order', []), ensure_ascii=False)
        notify_targets = json.dumps(data.get('notify_targets', []), ensure_ascii=False)
        scope_filter = json.dumps(data.get('scope_filter', {}), ensure_ascii=False)
    except Exception as e:
        return jsonify({'error': f'JSON字段格式错误: {str(e)}'}), 400
    
    try:
        cursor = db.execute('''INSERT INTO reminder_strategies 
                              (name, description, trigger_conditions, escalation_order,
                               cooldown_minutes, timeout_hours, notify_targets, scope_filter,
                               priority, status, created_by)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?)''',
                           (name, data.get('description', ''), trigger_conditions, escalation_order,
                            data.get('cooldown_minutes', 60), data.get('timeout_hours', 24),
                            notify_targets, scope_filter, data.get('priority', 0), operator_id))
        strategy_id = cursor.lastrowid
        
        save_strategy_snapshot(db, strategy_id, operator_id)
        add_strategy_log(db, strategy_id, STRATEGY_ACTION['CREATE'], operator_id, {'name': name})
        
        db.commit()
        
        strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
        enrich_strategy(db, strategy)
        return jsonify(strategy), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'创建失败: {str(e)}'}), 500

@app.route('/api/strategies/<int:strategy_id>', methods=['PUT'])
def update_strategy(strategy_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = data.get('operator_id')
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    if not strategy:
        return jsonify({'error': '策略不存在'}), 404
    
    if strategy['status'] == 'ACTIVE':
        return jsonify({'error': '已启用的策略不能直接修改，请先停用'}), 400
    
    name = data.get('name', strategy['name']).strip()
    if name != strategy['name']:
        existing = db.execute('SELECT * FROM reminder_strategies WHERE name = ? AND id != ?', (name, strategy_id)).fetchone()
        if existing:
            return jsonify({'error': '策略名称已存在'}), 409
    
    try:
        trigger_conditions = json.dumps(data.get('trigger_conditions', json.loads(strategy['trigger_conditions'])), ensure_ascii=False)
        escalation_order = json.dumps(data.get('escalation_order', json.loads(strategy['escalation_order'])), ensure_ascii=False)
        notify_targets = json.dumps(data.get('notify_targets', json.loads(strategy['notify_targets'])), ensure_ascii=False)
        scope_filter = json.dumps(data.get('scope_filter', json.loads(strategy['scope_filter'])), ensure_ascii=False)
    except Exception as e:
        return jsonify({'error': f'JSON字段格式错误: {str(e)}'}), 400
    
    old_version = strategy['version']
    new_version = old_version + 1
    
    db.execute('''UPDATE reminder_strategies SET
                  name = ?, description = ?, trigger_conditions = ?, escalation_order = ?,
                  cooldown_minutes = ?, timeout_hours = ?, notify_targets = ?, scope_filter = ?,
                  priority = ?, version = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                  WHERE id = ?''',
               (name, data.get('description', strategy.get('description', '')),
                trigger_conditions, escalation_order,
                data.get('cooldown_minutes', strategy['cooldown_minutes']),
                data.get('timeout_hours', strategy['timeout_hours']),
                notify_targets, scope_filter,
                data.get('priority', strategy['priority']),
                new_version, operator_id, strategy_id))
    
    save_strategy_snapshot(db, strategy_id, operator_id)
    add_strategy_log(db, strategy_id, STRATEGY_ACTION['UPDATE'], operator_id,
                     {'old_version': old_version, 'new_version': new_version, 'changes': data})
    
    db.commit()
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    enrich_strategy(db, strategy)
    return jsonify(strategy)

@app.route('/api/strategies/<int:strategy_id>/enable', methods=['POST'])
def enable_strategy(strategy_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = data.get('operator_id')
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    if not strategy:
        return jsonify({'error': '策略不存在'}), 404
    
    if strategy['status'] == 'ACTIVE':
        return jsonify({'error': '策略已经是启用状态'}), 400
    
    db.execute("UPDATE reminder_strategies SET status = 'ACTIVE', updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
               (operator_id, strategy_id))
    
    add_strategy_log(db, strategy_id, STRATEGY_ACTION['ENABLE'], operator_id)
    db.commit()
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    enrich_strategy(db, strategy)
    return jsonify(strategy)

@app.route('/api/strategies/<int:strategy_id>/disable', methods=['POST'])
def disable_strategy(strategy_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = data.get('operator_id')
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    if not strategy:
        return jsonify({'error': '策略不存在'}), 404
    
    if strategy['status'] != 'ACTIVE':
        return jsonify({'error': '策略不是启用状态'}), 400
    
    db.execute("UPDATE reminder_strategies SET status = 'INACTIVE', updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
               (operator_id, strategy_id))
    
    add_strategy_log(db, strategy_id, STRATEGY_ACTION['DISABLE'], operator_id)
    db.commit()
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    enrich_strategy(db, strategy)
    return jsonify(strategy)

@app.route('/api/strategies/<int:strategy_id>/rollback', methods=['POST'])
def rollback_strategy(strategy_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = data.get('operator_id')
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    if not strategy:
        return jsonify({'error': '策略不存在'}), 404
    
    if strategy['status'] == 'ACTIVE':
        return jsonify({'error': '已启用的策略不能回滚，请先停用'}), 400
    
    target_version = data.get('target_version')
    if target_version is None:
        target_version = strategy['version'] - 1
    
    if target_version < 1:
        return jsonify({'error': '没有更早的版本可以回滚'}), 400
    
    snapshot = db.execute('''SELECT * FROM reminder_strategy_snapshots 
                             WHERE strategy_id = ? AND version = ? 
                             ORDER BY id DESC LIMIT 1''',
                          (strategy_id, target_version)).fetchone()
    if not snapshot:
        return jsonify({'error': f'找不到版本 {target_version} 的快照'}), 404
    
    try:
        snap_data = json.loads(snapshot['snapshot_data'])
    except Exception as e:
        return jsonify({'error': f'快照数据解析失败: {str(e)}'}), 500
    
    old_version = strategy['version']
    new_version = target_version
    
    db.execute('''UPDATE reminder_strategies SET
                  name = ?, description = ?, trigger_conditions = ?, escalation_order = ?,
                  cooldown_minutes = ?, timeout_hours = ?, notify_targets = ?, scope_filter = ?,
                  priority = ?, status = ?, version = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                  WHERE id = ?''',
               (snap_data['name'], snap_data.get('description', ''),
                snap_data['trigger_conditions'], snap_data['escalation_order'],
                snap_data['cooldown_minutes'], snap_data['timeout_hours'],
                snap_data['notify_targets'], snap_data['scope_filter'],
                snap_data['priority'], snap_data['status'],
                new_version, operator_id, strategy_id))
    
    save_strategy_snapshot(db, strategy_id, operator_id)
    add_strategy_log(db, strategy_id, STRATEGY_ACTION['ROLLBACK'], operator_id,
                     {'from_version': old_version, 'to_version': target_version, 'new_version': new_version})
    
    db.commit()
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    enrich_strategy(db, strategy)
    return jsonify(strategy)

@app.route('/api/strategies/<int:strategy_id>/preview', methods=['POST'])
def preview_strategy(strategy_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = data.get('operator_id')
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    if strategy_id == 0:
        strategy_data = data.get('strategy', {k: v for k, v in data.items() if k != 'operator_id'})
        if not strategy_data or 'name' not in strategy_data:
            return jsonify({'error': '草稿预演需要提供策略数据，包含name字段'}), 400
        
        strategy = {
            'id': 0,
            'name': strategy_data.get('name', '草稿策略'),
            'description': strategy_data.get('description', ''),
            'trigger_conditions': json.dumps(strategy_data.get('trigger_conditions', {}), ensure_ascii=False),
            'escalation_order': json.dumps(strategy_data.get('escalation_order', []), ensure_ascii=False),
            'cooldown_minutes': strategy_data.get('cooldown_minutes', 60),
            'timeout_hours': strategy_data.get('timeout_hours', 24),
            'notify_targets': json.dumps(strategy_data.get('notify_targets', []), ensure_ascii=False),
            'scope_filter': json.dumps(strategy_data.get('scope_filter', {}), ensure_ascii=False),
            'priority': strategy_data.get('priority', 0),
            'status': 'DRAFT',
            'version': strategy_data.get('version', 1),
            'created_by': operator_id,
            'updated_by': operator_id,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        enrich_strategy(db, strategy)
    else:
        strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
        if not strategy:
            return jsonify({'error': '策略不存在'}), 404
        enrich_strategy(db, strategy)
    
    matching_reviews = get_reviews_for_strategy(db, strategy)
    
    active_strategies = db.execute("SELECT * FROM reminder_strategies WHERE status = 'ACTIVE'").fetchall()
    for s in active_strategies:
        enrich_strategy(db, s)
    
    if strategy['status'] != 'ACTIVE':
        all_strategies = active_strategies + [strategy]
    else:
        all_strategies = active_strategies
    
    conflict_results = check_conflict_and_resolve(db, matching_reviews, all_strategies)
    
    preview_details = []
    for result in conflict_results:
        review = result['review']
        selected = result['selected_strategy']
        
        escalation_level, escalation_step = calculate_escalation_level(db, selected, review)
        in_cooldown, cooldown_remaining = check_cooldown(db, selected, review)
        
        pending_reminders = db.execute('''SELECT COUNT(*) as cnt FROM reminders 
                                          WHERE review_id = ? AND status = 'PENDING' ''',
                                       (review['id'],)).fetchone()['cnt']
        
        last_reminder = db.execute('''SELECT MAX(created_at) as last_at FROM reminders 
                                      WHERE review_id = ?''', (review['id'],)).fetchone()
        
        preview_details.append({
            **result,
            'escalation_level': escalation_level,
            'escalation_step': escalation_step,
            'in_cooldown': in_cooldown,
            'cooldown_remaining_minutes': round(cooldown_remaining, 1) if cooldown_remaining else None,
            'pending_reminders': pending_reminders,
            'last_reminded_at': last_reminder['last_at'] if last_reminder else None,
            'will_trigger_reminder': not in_cooldown,
            'will_escalate': escalation_level > 0
        })
    
    return jsonify({
        'strategy': strategy,
        'total_matches': len(preview_details),
        'conflict_count': sum(1 for r in preview_details if r['conflict']),
        'will_trigger_count': sum(1 for r in preview_details if r['will_trigger_reminder']),
        'will_escalate_count': sum(1 for r in preview_details if r['will_escalate']),
        'details': preview_details
    })

@app.route('/api/strategies/import', methods=['POST'])
def import_strategies():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = data.get('operator_id')
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    import_data = data.get('strategies')
    if not import_data or not isinstance(import_data, list):
        return jsonify({'error': '导入数据格式错误，应为策略数组'}), 400
    
    import_mode = data.get('import_mode', 'new')
    preserve_version = data.get('preserve_version', False)
    
    if import_mode not in ['new', 'restore']:
        return jsonify({'error': 'import_mode只能是new或restore'}), 400
    
    validation_errors = []
    valid_strategies = []
    
    for idx, s_data in enumerate(import_data):
        errors = []
        if not s_data.get('name'):
            errors.append('策略名称不能为空')
        
        if not isinstance(s_data.get('trigger_conditions', {}), dict):
            errors.append('trigger_conditions应为对象')
        
        if not isinstance(s_data.get('escalation_order', []), list):
            errors.append('escalation_order应为数组')
        
        if not isinstance(s_data.get('notify_targets', []), list):
            errors.append('notify_targets应为数组')
        
        if not isinstance(s_data.get('scope_filter', {}), dict):
            errors.append('scope_filter应为对象')
        
        if not isinstance(s_data.get('cooldown_minutes', 60), (int, float)) or s_data.get('cooldown_minutes', 60) <= 0:
            errors.append('cooldown_minutes应为正整数')
        
        if not isinstance(s_data.get('timeout_hours', 24), (int, float)) or s_data.get('timeout_hours', 24) <= 0:
            errors.append('timeout_hours应为正整数')
        
        if preserve_version:
            source_version = s_data.get('version', 1)
            if not isinstance(source_version, int) or source_version < 1:
                errors.append('版本号必须是正整数')
        
        existing = db.execute('SELECT * FROM reminder_strategies WHERE name = ?', (s_data['name'],)).fetchone()
        if existing:
            errors.append(f'策略名称"{s_data["name"]}"已存在')
        
        for other in valid_strategies:
            if other['name'] == s_data['name']:
                errors.append(f'导入数据中存在重复的策略名称"{s_data["name"]}"')
                break
        
        if errors:
            validation_errors.append({'index': idx, 'name': s_data.get('name', f'策略{idx}'), 'errors': errors})
        else:
            valid_strategies.append(s_data)
    
    if validation_errors:
        return jsonify({
            'error': '导入数据校验失败',
            'errors': validation_errors,
            'validation_errors': validation_errors,
            'valid_count': len(valid_strategies)
        }), 400
    
    imported_ids = []
    import_results = []
    try:
        for s_data in valid_strategies:
            trigger_conditions = json.dumps(s_data.get('trigger_conditions', {}), ensure_ascii=False)
            escalation_order = json.dumps(s_data.get('escalation_order', []), ensure_ascii=False)
            notify_targets = json.dumps(s_data.get('notify_targets', []), ensure_ascii=False)
            scope_filter = json.dumps(s_data.get('scope_filter', {}), ensure_ascii=False)
            
            source_version = s_data.get('version', 1)
            if preserve_version:
                target_version = source_version
            else:
                target_version = 1
            
            source_status = s_data.get('status', 'DRAFT')
            target_status = 'DRAFT'
            
            export_info = s_data.get('export_info', {})
            original_id = export_info.get('original_id')
            original_version = export_info.get('original_version', source_version)
            original_is_active = export_info.get('original_is_active', False)
            
            cursor = db.execute('''INSERT INTO reminder_strategies 
                                  (name, description, trigger_conditions, escalation_order,
                                   cooldown_minutes, timeout_hours, notify_targets, scope_filter,
                                   priority, status, version, created_by)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                               (s_data['name'], s_data.get('description', ''),
                                trigger_conditions, escalation_order,
                                s_data.get('cooldown_minutes', 60),
                                s_data.get('timeout_hours', 24),
                                notify_targets, scope_filter,
                                s_data.get('priority', 0),
                                target_status, target_version, operator_id))
            strategy_id = cursor.lastrowid
            imported_ids.append(strategy_id)
            
            version_history = s_data.get('version_history', [])
            if preserve_version and version_history:
                for vh in version_history:
                    v = vh.get('version')
                    snapshot = vh.get('snapshot', {})
                    if v and snapshot:
                        try:
                            snap_data_str = json.dumps({
                                'name': snapshot.get('name', s_data['name']),
                                'description': snapshot.get('description', ''),
                                'trigger_conditions': json.dumps(snapshot.get('trigger_conditions', {}), ensure_ascii=False),
                                'escalation_order': json.dumps(snapshot.get('escalation_order', []), ensure_ascii=False),
                                'cooldown_minutes': snapshot.get('cooldown_minutes', 60),
                                'timeout_hours': snapshot.get('timeout_hours', 24),
                                'notify_targets': json.dumps(snapshot.get('notify_targets', []), ensure_ascii=False),
                                'scope_filter': json.dumps(snapshot.get('scope_filter', {}), ensure_ascii=False),
                                'priority': snapshot.get('priority', 0),
                                'status': snapshot.get('status', 'DRAFT'),
                                'version': v
                            }, ensure_ascii=False)
                            
                            db.execute('''INSERT OR REPLACE INTO reminder_strategy_snapshots 
                                          (strategy_id, snapshot_data, version, created_by, created_at)
                                          VALUES (?, ?, ?, ?, ?)''',
                                       (strategy_id, snap_data_str, v, operator_id, 
                                        vh.get('created_at', datetime.now().isoformat())))
                        except Exception:
                            pass
            else:
                save_strategy_snapshot(db, strategy_id, operator_id)
            
            import_detail = {
                'imported_name': s_data['name'],
                'source_version': source_version,
                'target_version': target_version,
                'preserve_version': preserve_version,
                'import_mode': import_mode,
                'original_id': original_id,
                'original_version': original_version,
                'original_is_active': original_is_active,
                'source_is_active': s_data.get('is_active', False),
                'source_effective_version': s_data.get('effective_version'),
                'target_status': target_status,
                'version_note': f'导入后版本为v{target_version}，状态为{target_status}。{"保留了原始版本号" if preserve_version else "从v1开始"}。'
            }
            add_strategy_log(db, strategy_id, STRATEGY_ACTION['IMPORT'], operator_id, import_detail)
            
            import_results.append({
                'strategy_id': strategy_id,
                'name': s_data['name'],
                'source_version': source_version,
                'imported_version': target_version,
                'preserve_version': preserve_version,
                'original_id': original_id,
                'original_version': original_version,
                'original_is_active': original_is_active,
                'is_active': False,
                'effective_version': None,
                'version_note': import_detail['version_note'],
                'activation_note': '导入的策略默认为草稿状态，如需生效请手动启用'
            })
        
        db.commit()
        
        strategies = []
        for idx, sid in enumerate(imported_ids):
            s = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (sid,)).fetchone()
            enrich_strategy(db, s)
            strategies.append(s)
            import_results[idx]['strategy'] = s
        
        active_versions = [s['version'] for s in strategies if s['status'] == 'ACTIVE']
        max_active_version = max(active_versions) if active_versions else None
        
        return jsonify({
            'success': True,
            'imported_count': len(imported_ids),
            'imported_ids': imported_ids,
            'import_mode': import_mode,
            'preserve_version': preserve_version,
            'max_active_version': max_active_version,
            'active_count': len(active_versions),
            'version_semantics_note': '导入的策略默认为草稿(DRAFT)状态，版本号' + ('保留了原始版本' if preserve_version else '从v1开始') + '。只有启用(ACTIVE)的策略才是生效版本。',
            'import_results': import_results,
            'strategies': strategies
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'导入失败: {str(e)}'}), 500

@app.route('/api/strategies/export', methods=['GET'])
def export_strategies():
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = request.args.get('operator_id', type=int)
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy_ids = request.args.get('ids')
    if strategy_ids:
        ids = [int(x) for x in strategy_ids.split(',') if x.strip()]
        placeholders = ','.join(['?'] * len(ids))
        query = f'SELECT * FROM reminder_strategies WHERE id IN ({placeholders})'
        strategies = db.execute(query, ids).fetchall()
    else:
        strategies = db.execute('SELECT * FROM reminder_strategies ORDER BY priority DESC, created_at DESC').fetchall()
    
    export_data = []
    for s in strategies:
        add_strategy_log(db, s['id'], STRATEGY_ACTION['EXPORT'], operator_id, {'exported_at': datetime.now().isoformat()})
        
        snapshots = db.execute('''SELECT version, created_at, snapshot_data 
                                  FROM reminder_strategy_snapshots 
                                  WHERE strategy_id = ? 
                                  ORDER BY version''', (s['id'],)).fetchall()
        
        version_history = []
        for snap in snapshots:
            try:
                snap_data = json.loads(snap['snapshot_data'])
            except Exception:
                snap_data = {}
            version_history.append({
                'version': snap['version'],
                'created_at': snap['created_at'],
                'snapshot': snap_data
            })
        
        export_data.append({
            'name': s['name'],
            'description': s['description'],
            'trigger_conditions': json.loads(s['trigger_conditions']) if s['trigger_conditions'] else {},
            'escalation_order': json.loads(s['escalation_order']) if s['escalation_order'] else [],
            'cooldown_minutes': s['cooldown_minutes'],
            'timeout_hours': s['timeout_hours'],
            'notify_targets': json.loads(s['notify_targets']) if s['notify_targets'] else [],
            'scope_filter': json.loads(s['scope_filter']) if s['scope_filter'] else {},
            'priority': s['priority'],
            'status': s['status'],
            'version': s['version'],
            'is_active': s['status'] == 'ACTIVE',
            'created_at': s['created_at'],
            'updated_at': s['updated_at'],
            'version_history': version_history,
            'effective_version': s['version'] if s['status'] == 'ACTIVE' else None,
            'export_info': {
                'exported_at': datetime.now().isoformat(),
                'exported_by': db.execute('SELECT username FROM users WHERE id = ?', (operator_id,)).fetchone()['username'],
                'original_id': s['id'],
                'original_created_at': s['created_at'],
                'original_updated_at': s['updated_at'],
                'original_version': s['version'],
                'original_status': s['status'],
                'original_is_active': s['status'] == 'ACTIVE'
            }
        })
    
    db.commit()
    
    operator = db.execute('SELECT username, role FROM users WHERE id = ?', (operator_id,)).fetchone()
    exported_by = f"{operator['username']} ({operator['role']})" if operator else str(operator_id)
    
    active_versions = [s['version'] for s in strategies if s['status'] == 'ACTIVE']
    max_active_version = max(active_versions) if active_versions else None
    
    return jsonify({
        'version': '2.0',
        'export_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'exported_at': datetime.now().isoformat(),
        'exported_by': exported_by,
        'exported_count': len(export_data),
        'export_version': '2.0',
        'max_active_version': max_active_version,
        'active_count': len(active_versions),
        'draft_count': len([s for s in strategies if s['status'] == 'DRAFT']),
        'inactive_count': len([s for s in strategies if s['status'] == 'INACTIVE']),
        'version_semantics_note': '导入时默认从v1开始，可选择导入为新版本或恢复原版本号。只有状态为ACTIVE的版本才是生效版本。',
        'strategies': export_data
    })

@app.route('/api/strategies/logs', methods=['GET'])
def get_strategy_logs():
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = request.args.get('operator_id', type=int)
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy_id = request.args.get('strategy_id', type=int)
    query = '''SELECT l.*, s.name as strategy_name, u.username as operator_name
               FROM reminder_strategy_logs l
               JOIN reminder_strategies s ON l.strategy_id = s.id
               JOIN users u ON l.operator_id = u.id'''
    params = []
    if strategy_id:
        query += ' WHERE l.strategy_id = ?'
        params.append(strategy_id)
    query += ' ORDER BY l.created_at DESC LIMIT 100'
    
    logs = db.execute(query, params).fetchall()
    for log in logs:
        if log.get('detail'):
            try:
                log['detail'] = json.loads(log['detail'])
            except Exception:
                pass
    return jsonify(logs)

@app.route('/api/strategies/<int:strategy_id>/undo', methods=['POST'])
def undo_last_change(strategy_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = data.get('operator_id')
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    if not strategy:
        return jsonify({'error': '策略不存在'}), 404
    
    if strategy['status'] == 'ACTIVE':
        return jsonify({'error': '已启用的策略不能撤销，请先停用'}), 400
    
    last_log = db.execute('''SELECT * FROM reminder_strategy_logs 
                             WHERE strategy_id = ? AND action != ?
                             ORDER BY id DESC LIMIT 1''',
                          (strategy_id, STRATEGY_ACTION['UNDO'])).fetchone()
    
    if not last_log:
        return jsonify({'error': '没有可撤销的操作'}), 400
    
    undoable_actions = [STRATEGY_ACTION['UPDATE'], STRATEGY_ACTION['ENABLE'], STRATEGY_ACTION['DISABLE'], STRATEGY_ACTION['ROLLBACK']]
    if last_log['action'] not in undoable_actions:
        return jsonify({'error': f'最近一次操作"{last_log["action"]}"不支持撤销'}), 400
    
    current_version = strategy['version']
    if current_version <= 1:
        return jsonify({'error': '当前为初始版本，无法撤销'}), 400
    
    target_version = current_version - 1
    
    snapshot = db.execute('''SELECT * FROM reminder_strategy_snapshots 
                             WHERE strategy_id = ? AND version = ? 
                             ORDER BY id DESC LIMIT 1''',
                          (strategy_id, target_version)).fetchone()
    if not snapshot:
        return jsonify({'error': f'找不到版本 {target_version} 的快照，无法撤销'}), 404
    
    try:
        snap_data = json.loads(snapshot['snapshot_data'])
    except Exception as e:
        return jsonify({'error': f'快照数据解析失败: {str(e)}'}), 500
    
    old_version = current_version
    new_version = target_version
    
    db.execute('''UPDATE reminder_strategies SET
                  name = ?, description = ?, trigger_conditions = ?, escalation_order = ?,
                  cooldown_minutes = ?, timeout_hours = ?, notify_targets = ?, scope_filter = ?,
                  priority = ?, status = ?, version = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                  WHERE id = ?''',
               (snap_data['name'], snap_data.get('description', ''),
                snap_data['trigger_conditions'], snap_data['escalation_order'],
                snap_data['cooldown_minutes'], snap_data['timeout_hours'],
                snap_data['notify_targets'], snap_data['scope_filter'],
                snap_data['priority'], snap_data['status'],
                new_version, operator_id, strategy_id))
    
    save_strategy_snapshot(db, strategy_id, operator_id)
    add_strategy_log(db, strategy_id, STRATEGY_ACTION['UNDO'], operator_id,
                     {'undone_action': last_log['action'], 'from_version': old_version, 'to_version': target_version})
    
    db.commit()
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    enrich_strategy(db, strategy)
    return jsonify({
        'success': True,
        'message': f'已撤销操作"{last_log["action"]}"，版本从v{old_version}回退到v{new_version}',
        'undone_action': last_log['action'],
        'from_version': old_version,
        'to_version': new_version,
        'strategy': strategy
    })

@app.route('/api/strategies/active', methods=['GET'])
def get_active_strategies():
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = request.args.get('operator_id', type=int)
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategies = db.execute("SELECT * FROM reminder_strategies WHERE status = 'ACTIVE' ORDER BY priority DESC, created_at DESC").fetchall()
    for s in strategies:
        enrich_strategy(db, s)
    
    return jsonify({
        'count': len(strategies),
        'current_effective_version': max([s['version'] for s in strategies]) if strategies else 0,
        'strategies': strategies
    })

@app.route('/api/strategies/<int:strategy_id>/effective-version', methods=['GET'])
def get_strategy_effective_version(strategy_id):
    db = get_db()
    db.row_factory = dict_factory
    
    operator_id = request.args.get('operator_id', type=int)
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    ok, err = check_strategy_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    strategy = db.execute('SELECT * FROM reminder_strategies WHERE id = ?', (strategy_id,)).fetchone()
    if not strategy:
        return jsonify({'error': '策略不存在'}), 404
    
    enrich_strategy(db, strategy)
    
    snapshots = db.execute('''SELECT s.*, u.username as creator_name
                              FROM reminder_strategy_snapshots s JOIN users u ON s.created_by = u.id
                              WHERE s.strategy_id = ? ORDER BY s.created_at DESC''',
                           (strategy_id,)).fetchall()
    
    version_history = []
    for snap in snapshots:
        try:
            snap_data = json.loads(snap['snapshot_data'])
        except Exception:
            snap_data = {}
        
        version_history.append({
            'version': snap['version'],
            'snapshot_data': snap_data,
            'created_at': snap['created_at'],
            'creator_name': snap['creator_name'],
            'is_effective': snap['version'] == strategy['version'] and strategy['status'] == 'ACTIVE'
        })
    
    return jsonify({
        'strategy_id': strategy_id,
        'strategy_name': strategy['name'],
        'current_version': strategy['version'],
        'is_active': strategy['status'] == 'ACTIVE',
        'effective_version': strategy['version'] if strategy['status'] == 'ACTIVE' else None,
        'effective_version_note': '当前版本即为生效版本' if strategy['status'] == 'ACTIVE' else '策略未启用，无生效版本',
        'version_history': version_history
    })

def enrich_connection_config(db, config):
    config['status_name'] = CONNECTION_STATUS_NAME.get(config.get('last_probe_status', 'UNKNOWN'), config.get('last_probe_status', 'UNKNOWN'))
    config['entry_url'] = f"{config['protocol']}://{config['service_host']}:{config['service_port']}{config['entry_path']}"
    if config.get('current_operator_id'):
        op = db.execute('SELECT username, role FROM users WHERE id = ?', (config['current_operator_id'],)).fetchone()
        config['operator_name'] = op['username'] if op else None
        config['operator_role'] = op['role'] if op else None
        config['operator_role_name'] = USER_ROLES.get(op['role'], op['role']) if op else None
    else:
        config['operator_name'] = None
        config['operator_role'] = None
        config['operator_role_name'] = None
    creator = db.execute('SELECT username FROM users WHERE id = ?', (config['created_by'],)).fetchone() if config.get('created_by') else None
    config['creator_name'] = creator['username'] if creator else None
    if config.get('updated_by'):
        updator = db.execute('SELECT username FROM users WHERE id = ?', (config['updated_by'],)).fetchone()
        config['updator_name'] = updator['username'] if updator else None
    else:
        config['updator_name'] = None
    if config.get('published_by'):
        publisher = db.execute('SELECT username FROM users WHERE id = ?', (config['published_by'],)).fetchone()
        config['publisher_name'] = publisher['username'] if publisher else None
    else:
        config['publisher_name'] = None
    return config

def add_connection_log(db, config_id, profile_name, action, operator_id, detail=None):
    op = db.execute('SELECT username FROM users WHERE id = ?', (operator_id,)).fetchone() if operator_id else None
    operator_name = op['username'] if op else None
    db.execute('''INSERT INTO connection_logs 
                  (config_id, profile_name, action, operator_id, operator_name, detail)
                  VALUES (?, ?, ?, ?, ?, ?)''',
               (config_id, profile_name, action, operator_id, operator_name,
                json.dumps(detail, ensure_ascii=False) if detail else None))

def save_connection_snapshot(db, config_id, operator_id, version=None):
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    if not config:
        return
    snapshot_version = version if version else config['config_version']
    snapshot_data = {
        'profile_name': config['profile_name'],
        'service_host': config['service_host'],
        'service_port': config['service_port'],
        'entry_path': config['entry_path'],
        'protocol': config['protocol'],
        'current_operator_id': config['current_operator_id'],
        'is_default': config['is_default'],
        'is_published': config['is_published'],
        'last_probe_status': config['last_probe_status'],
        'last_probe_at': config['last_probe_at'],
        'config_version': snapshot_version
    }
    db.execute('''INSERT INTO connection_config_snapshots 
                  (config_id, snapshot_data, version, created_by)
                  VALUES (?, ?, ?, ?)''',
               (config_id, json.dumps(snapshot_data, ensure_ascii=False), snapshot_version, operator_id))

def check_connection_publish_permission(operator_id, db):
    operator = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not operator:
        return False, '操作员不存在'
    if operator['role'] not in CONNECTION_PUBLISH_PERMISSION_ROLES:
        return False, f'只有{",".join(CONNECTION_PUBLISH_PERMISSION_ROLES)}角色才能发布连接配置'
    return True, None

def run_diagnostics(db, config_id, host, port):
    diagnostics = []
    
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            diag = {
                'diagnostic_type': 'PORT_CONNECTIVITY',
                'status': 'PASS',
                'message': f'端口 {port} 可正常连接',
                'suggestion': None,
                'recovery_action': None
            }
        else:
            diag = {
                'diagnostic_type': 'PORT_CONNECTIVITY',
                'status': 'FAIL',
                'message': f'无法连接到端口 {port}，错误码: {result}',
                'suggestion': '请检查服务是否启动、端口是否正确、防火墙是否放行',
                'recovery_action': '检查服务进程: netstat -ano | findstr /R /C":{port}" ; 启动服务: python app.py'
            }
    except Exception as e:
        diag = {
            'diagnostic_type': 'PORT_CONNECTIVITY',
            'status': 'ERROR',
            'message': f'端口检测异常: {str(e)}',
            'suggestion': '请检查网络连接和服务地址配置',
            'recovery_action': '验证IP地址格式是否正确，尝试使用 127.0.0.1'
        }
    diagnostics.append(diag)
    
    try:
        ip_valid = True
        try:
            socket.inet_aton(host)
        except socket.error:
            try:
                socket.gethostbyname(host)
            except socket.error:
                ip_valid = False
        if ip_valid:
            diag = {
                'diagnostic_type': 'HOST_RESOLUTION',
                'status': 'PASS',
                'message': f'主机地址 {host} 可解析',
                'suggestion': None,
                'recovery_action': None
            }
        else:
            diag = {
                'diagnostic_type': 'HOST_RESOLUTION',
                'status': 'FAIL',
                'message': f'无法解析主机地址 {host}',
                'suggestion': '请检查主机名拼写或DNS配置',
                'recovery_action': '尝试使用 127.0.0.1 或 localhost 作为主机地址'
            }
    except Exception as e:
        diag = {
            'diagnostic_type': 'HOST_RESOLUTION',
            'status': 'ERROR',
            'message': f'主机解析检测异常: {str(e)}',
            'suggestion': None,
            'recovery_action': None
        }
    diagnostics.append(diag)
    
    if not (0 < port < 65536):
        diag = {
            'diagnostic_type': 'PORT_VALIDATION',
            'status': 'FAIL',
            'message': f'端口号 {port} 不在有效范围内 (1-65535)',
            'suggestion': '请输入有效的端口号',
            'recovery_action': '默认端口为 5002，请修改为有效端口'
        }
    else:
        diag = {
            'diagnostic_type': 'PORT_VALIDATION',
            'status': 'PASS',
            'message': f'端口号 {port} 格式有效',
            'suggestion': None,
            'recovery_action': None
        }
    diagnostics.append(diag)
    
    try:
        import urllib.request
        import urllib.error
        url = f"http://{host}:{port}/api/users"
        req = urllib.request.Request(url)
        urllib.request.urlopen(req, timeout=3)
        diag = {
            'diagnostic_type': 'API_RESPONSIVE',
            'status': 'PASS',
            'message': '后端API正常响应',
            'suggestion': None,
            'recovery_action': None
        }
    except urllib.error.URLError as e:
        diag = {
            'diagnostic_type': 'API_RESPONSIVE',
            'status': 'FAIL',
            'message': f'后端API无响应: {str(e)}',
            'suggestion': '请确认后端服务已启动且运行在正确的端口上',
            'recovery_action': '执行 python app.py 启动服务，等待 Flask 初始化完成后再探测'
        }
    except Exception as e:
        diag = {
            'diagnostic_type': 'API_RESPONSIVE',
            'status': 'WARN',
            'message': f'API检测跳过（非致命错误）: {str(e)}',
            'suggestion': '端口已连通但API检测异常，可能是路径问题',
            'recovery_action': '确认入口路径是否为 / 或其他自定义路径'
        }
    diagnostics.append(diag)
    
    for d in diagnostics:
        db.execute('''INSERT INTO connection_diagnostics 
                      (config_id, host, port, diagnostic_type, status, message, suggestion, recovery_action)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                   (config_id, host, port, d['diagnostic_type'], d['status'],
                    d['message'], d['suggestion'], d['recovery_action']))
    
    return diagnostics

@app.route('/api/connection/configs', methods=['GET'])
def get_connection_configs():
    db = get_db()
    db.row_factory = dict_factory
    configs = db.execute('SELECT * FROM connection_configs ORDER BY is_default DESC, created_at DESC').fetchall()
    for c in configs:
        enrich_connection_config(db, c)
    return jsonify(configs)

@app.route('/api/connection/configs/default', methods=['GET'])
def get_default_connection_config():
    db = get_db()
    db.row_factory = dict_factory
    config = db.execute('SELECT * FROM connection_configs WHERE is_default = 1 ORDER BY id DESC LIMIT 1').fetchone()
    if not config:
        config = db.execute('SELECT * FROM connection_configs ORDER BY id DESC LIMIT 1').fetchone()
    if not config:
        return jsonify({'error': '没有可用的连接配置'}), 404
    enrich_connection_config(db, config)
    return jsonify(config)

@app.route('/api/connection/configs', methods=['POST'])
def create_connection_config():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    operator_id = data.get('operator_id')
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    profile_name = data.get('profile_name', '').strip()
    if not profile_name:
        return jsonify({'error': '配置名称不能为空'}), 400
    
    existing = db.execute('SELECT * FROM connection_configs WHERE profile_name = ?', (profile_name,)).fetchone()
    if existing:
        return jsonify({'error': f'配置名称"{profile_name}"已存在'}), 409
    
    service_host = data.get('service_host', '127.0.0.1').strip()
    service_port = int(data.get('service_port', 5002))
    entry_path = data.get('entry_path', '/').strip() or '/'
    protocol = data.get('protocol', 'http').strip() or 'http'
    current_operator_id = data.get('current_operator_id')
    
    if current_operator_id:
        op = db.execute('SELECT id FROM users WHERE id = ?', (current_operator_id,)).fetchone()
        if not op:
            return jsonify({'error': '指定的操作人不存在'}), 400
    
    if data.get('is_default'):
        db.execute("UPDATE connection_configs SET is_default = 0, updated_at = CURRENT_TIMESTAMP")
    
    try:
        cursor = db.execute('''INSERT INTO connection_configs 
                              (profile_name, service_host, service_port, entry_path, protocol,
                               current_operator_id, is_default, config_version, created_by, updated_by)
                              VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)''',
                           (profile_name, service_host, service_port, entry_path, protocol,
                            current_operator_id, 1 if data.get('is_default') else 0,
                            operator_id, operator_id))
        config_id = cursor.lastrowid
        save_connection_snapshot(db, config_id, operator_id, 1)
        add_connection_log(db, config_id, profile_name, CONNECTION_LOG_ACTION['CREATE_CONFIG'],
                          operator_id, {'profile_name': profile_name})
        db.commit()
        config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
        enrich_connection_config(db, config)
        return jsonify(config), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'创建失败: {str(e)}'}), 500

@app.route('/api/connection/configs/<int:config_id>', methods=['GET', 'PUT', 'DELETE'])
def handle_connection_config(config_id):
    db = get_db()
    db.row_factory = dict_factory

    if request.method == 'GET':
        config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
        if not config:
            return jsonify({'error': '连接配置不存在'}), 404
        enrich_connection_config(db, config)
        snapshots = db.execute('SELECT * FROM connection_config_snapshots WHERE config_id = ? ORDER BY version DESC', (config_id,)).fetchall()
        for s in snapshots:
            if s.get('snapshot_data') and isinstance(s['snapshot_data'], str):
                try:
                    s['snapshot_data'] = json.loads(s['snapshot_data'])
                except (json.JSONDecodeError, TypeError):
                    pass
            creator = db.execute('SELECT username FROM users WHERE id = ?', (s.get('created_by'),)).fetchone()
            s['creator_name'] = creator['username'] if creator else '系统'
        logs = db.execute('''SELECT cl.*, u.username as operator_name FROM connection_logs cl
                           LEFT JOIN users u ON cl.operator_id = u.id
                           WHERE cl.config_id = ? ORDER BY cl.created_at DESC LIMIT 50''', (config_id,)).fetchall()
        latest_diag = db.execute('SELECT * FROM connection_diagnostics WHERE config_id = ? ORDER BY created_at DESC LIMIT 10', (config_id,)).fetchall()
        context_warnings = []
        if not config.get('current_operator_id'):
            context_warnings.append({'message': '未设置当前操作人', 'suggestion': '请在操作人管理区域设置操作人'})
        if config.get('last_probe_status') in ('UNAVAILABLE', 'TIMEOUT', 'ERROR'):
            context_warnings.append({'message': f'最近探测状态为{config.get("last_probe_status")}', 'suggestion': '请执行探测或检查服务是否正常运行'})
        return jsonify({'config': config, 'snapshots': snapshots, 'logs': logs, 'latest_diagnostics': latest_diag, 'context_warnings': context_warnings})

    if request.method == 'DELETE':
        config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
        if not config:
            return jsonify({'error': '连接配置不存在'}), 404
        profile_name = config['profile_name']
        db.execute('DELETE FROM connection_config_snapshots WHERE config_id = ?', (config_id,))
        db.execute('DELETE FROM connection_logs WHERE config_id = ?', (config_id,))
        db.execute('DELETE FROM connection_diagnostics WHERE config_id = ?', (config_id,))
        db.execute('DELETE FROM connection_configs WHERE id = ?', (config_id,))
        add_connection_log(db, None, profile_name, CONNECTION_LOG_ACTION['DELETE_CONFIG'],
                          None, {'deleted_config_id': config_id})
        db.commit()
        return jsonify({'message': '配置已删除'})

    data = request.json
    operator_id = data.get('updated_by') or data.get('operator_id')
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    if not config:
        return jsonify({'error': '连接配置不存在'}), 404
    
    profile_name = data.get('profile_name', config['profile_name']).strip()
    if profile_name != config['profile_name']:
        existing = db.execute('SELECT * FROM connection_configs WHERE profile_name = ? AND id != ?',
                             (profile_name, config_id)).fetchone()
        if existing:
            return jsonify({'error': f'配置名称"{profile_name}"已存在'}), 409
    
    current_operator_id = data.get('current_operator_id', config['current_operator_id'])
    if current_operator_id:
        op = db.execute('SELECT id FROM users WHERE id = ?', (current_operator_id,)).fetchone()
        if not op:
            return jsonify({'error': '指定的操作人不存在'}), 400
    
    if data.get('is_default'):
        db.execute("UPDATE connection_configs SET is_default = 0, updated_at = CURRENT_TIMESTAMP")
    
    old_version = config['config_version']
    new_version = old_version + 1
    
    db.execute('''UPDATE connection_configs SET
                  profile_name = ?, service_host = ?, service_port = ?, entry_path = ?, protocol = ?,
                  current_operator_id = ?, is_default = ?, config_version = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                  WHERE id = ?''',
               (profile_name,
                data.get('service_host', config['service_host']),
                int(data.get('service_port', config['service_port'])),
                data.get('entry_path', config['entry_path']) or '/',
                data.get('protocol', config['protocol']) or 'http',
                current_operator_id,
                1 if data.get('is_default') else config['is_default'],
                new_version, operator_id, config_id))
    
    save_connection_snapshot(db, config_id, operator_id, new_version)
    add_connection_log(db, config_id, profile_name, CONNECTION_LOG_ACTION['UPDATE_CONFIG'],
                      operator_id, {'from_version': old_version, 'to_version': new_version, 'changes': data})
    db.commit()
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    enrich_connection_config(db, config)
    return jsonify(config)

@app.route('/api/connection/configs/<int:config_id>/probe', methods=['POST'])
def probe_connection(config_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    operator_id = data.get('operator_id')
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    if not config:
        return jsonify({'error': '连接配置不存在'}), 404
    
    host = config['service_host']
    port = int(config['service_port'])
    
    diagnostics = run_diagnostics(db, config_id, host, port)
    
    all_pass = all(d['status'] == 'PASS' for d in diagnostics)
    any_fail = any(d['status'] == 'FAIL' for d in diagnostics)
    
    if all_pass:
        status = 'AVAILABLE'
        message = '连接正常，所有诊断项通过'
    elif any_fail:
        status = 'UNAVAILABLE'
        fail_msgs = [d['message'] for d in diagnostics if d['status'] == 'FAIL']
        message = '连接不可用: ' + '; '.join(fail_msgs)
    else:
        status = 'ERROR'
        message = '连接检测存在异常'
    
    db.execute('''UPDATE connection_configs SET
                  last_probe_status = ?, last_probe_at = CURRENT_TIMESTAMP,
                  last_probe_message = ?,
                  last_available_at = CASE WHEN ? = 'AVAILABLE' THEN CURRENT_TIMESTAMP ELSE last_available_at END,
                  updated_at = CURRENT_TIMESTAMP
                  WHERE id = ?''',
               (status, message, status, config_id))
    
    probe_detail = {
        'status': status,
        'host': host,
        'port': port,
        'diagnostics': diagnostics,
        'summary': {
            'pass': sum(1 for d in diagnostics if d['status'] == 'PASS'),
            'fail': sum(1 for d in diagnostics if d['status'] == 'FAIL'),
            'warn': sum(1 for d in diagnostics if d['status'] == 'WARN'),
            'error': sum(1 for d in diagnostics if d['status'] == 'ERROR'),
            'total': len(diagnostics)
        }
    }
    
    recovery_suggestions = []
    context_gaps = []
    
    if status != 'AVAILABLE':
        if not config['current_operator_id']:
            context_gaps.append('缺少当前操作人，请先选择操作人')
        for d in diagnostics:
            if d['status'] == 'FAIL':
                if d['suggestion']:
                    recovery_suggestions.append({
                        'item': d['diagnostic_type'],
                        'suggestion': d['suggestion'],
                        'action': d['recovery_action']
                    })
        if not context_gaps:
            context_gaps.append('连接上下文完整，但网络/服务层面存在问题')
    
    add_connection_log(db, config_id, config['profile_name'], CONNECTION_LOG_ACTION['PROBE_CONNECTION'],
                      operator_id, probe_detail)
    db.commit()
    
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    enrich_connection_config(db, config)
    
    return jsonify({
        'config': config,
        'probe': probe_detail,
        'status': status,
        'probe_status': status,
        'status_name': CONNECTION_STATUS_NAME.get(status, status),
        'message': message,
        'probe_message': message,
        'context_gaps': context_gaps,
        'recovery_suggestions': recovery_suggestions,
        'is_available': status == 'AVAILABLE'
    })

@app.route('/api/connection/configs/<int:config_id>/entry-url', methods=['GET'])
def get_connection_entry_url(config_id):
    db = get_db()
    db.row_factory = dict_factory
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    if not config:
        return jsonify({'error': '连接配置不存在'}), 404
    enrich_connection_config(db, config)
    context_warnings = []
    if not config['current_operator_id']:
        context_warnings.append('未设置操作人，访问策略中心等功能将被拦截')
    if config['last_probe_status'] != 'AVAILABLE' and config['last_probe_status'] != 'UNKNOWN':
        context_warnings.append(f'最近一次探测状态: {config["status_name"]}，可能无法正常访问')
    add_connection_log(db, config_id, config['profile_name'], CONNECTION_LOG_ACTION['OPEN_ENTRY'],
                      config['updated_by'] or config['created_by'],
                      {'entry_url': config['entry_url'], 'context_warnings': context_warnings})
    db.commit()
    return jsonify({
        'entry_url': config['entry_url'],
        'config': config,
        'context_warnings': context_warnings,
        'warnings': context_warnings,
        'can_access_strategies': bool(config['current_operator_id'] and config['operator_role'] == 'RECEIVER'),
        'operator_required': config['current_operator_id'] is None
    })

@app.route('/api/connection/configs/<int:config_id>/switch-operator', methods=['POST'])
def switch_connection_operator(config_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    operator_id = data.get('operator_id')
    new_operator_id = data.get('new_operator_id')
    if not operator_id or not new_operator_id:
        return jsonify({'error': '缺少operator_id或new_operator_id参数'}), 400
    
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    if not config:
        return jsonify({'error': '连接配置不存在'}), 404
    
    new_op = db.execute('SELECT * FROM users WHERE id = ?', (new_operator_id,)).fetchone()
    if not new_op:
        return jsonify({'error': '新操作人不存在'}), 404
    
    old_version = config['config_version']
    new_version = old_version + 1
    
    db.execute('''UPDATE connection_configs SET
                  current_operator_id = ?, config_version = ?,
                  updated_by = ?, updated_at = CURRENT_TIMESTAMP
                  WHERE id = ?''',
               (new_operator_id, new_version, operator_id, config_id))
    
    save_connection_snapshot(db, config_id, operator_id, new_version)
    add_connection_log(db, config_id, config['profile_name'], CONNECTION_LOG_ACTION['SWITCH_OPERATOR'],
                      operator_id,
                      {'from_operator_id': config['current_operator_id'],
                       'to_operator_id': new_operator_id,
                       'to_operator_name': new_op['username'],
                       'to_operator_role': new_op['role'],
                       'version_change': f'v{old_version}→v{new_version}'})
    db.commit()
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    enrich_connection_config(db, config)
    return jsonify({
        'config': config,
        'can_access_strategies': config['operator_role'] == 'RECEIVER',
        'message': f'操作人已切换为 {config["operator_name"]} ({config["operator_role_name"]})'
    })

@app.route('/api/connection/export', methods=['GET'])
def export_connection_configs():
    db = get_db()
    db.row_factory = dict_factory
    operator_id = request.args.get('operator_id', type=int)
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    op = db.execute('SELECT username, role FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not op:
        return jsonify({'error': '操作员不存在'}), 404
    
    config_ids = request.args.get('ids') or request.args.get('config_ids')
    if config_ids:
        ids = [int(x) for x in config_ids.split(',') if x.strip()]
        placeholders = ','.join(['?'] * len(ids))
        configs = db.execute(f'SELECT * FROM connection_configs WHERE id IN ({placeholders})', ids).fetchall()
    else:
        configs = db.execute('SELECT * FROM connection_configs ORDER BY is_default DESC, created_at DESC').fetchall()
    
    export_data = []
    for c in configs:
        snapshots = db.execute('''SELECT version, created_at, snapshot_data 
                                  FROM connection_config_snapshots 
                                  WHERE config_id = ? ORDER BY version''', (c['id'],)).fetchall()
        version_history = []
        for snap in snapshots:
            try:
                snap_data = json.loads(snap['snapshot_data'])
            except Exception:
                snap_data = {}
            version_history.append({
                'version': snap['version'],
                'created_at': snap['created_at'],
                'snapshot': snap_data
            })
        export_data.append({
            'profile_name': c['profile_name'],
            'service_host': c['service_host'],
            'service_port': c['service_port'],
            'entry_path': c['entry_path'],
            'protocol': c['protocol'],
            'is_default': bool(c['is_default']),
            'is_published': bool(c['is_published']),
            'config_version': c['config_version'],
            'status': c.get('last_probe_status', 'UNKNOWN'),
            'last_probe_at': c['last_probe_at'],
            'last_available_at': c['last_available_at'],
            'version_history': version_history,
            'export_info': {
                'original_id': c['id'],
                'original_created_at': c['created_at'],
                'original_updated_at': c['updated_at'],
                'original_config_version': c['config_version']
            }
        })
        add_connection_log(db, c['id'], c['profile_name'], CONNECTION_LOG_ACTION['EXPORT_CONFIG'],
                          operator_id, {'exported_at': datetime.now().isoformat()})
    
    db.commit()
    return jsonify({
        'version': '1.0',
        'export_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'exported_by': f"{op['username']} ({USER_ROLES.get(op['role'], op['role'])})",
        'exported_count': len(export_data),
        'configs': export_data
    })

@app.route('/api/connection/import', methods=['POST'])
def import_connection_configs():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    operator_id = data.get('operator_id')
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    op = db.execute('SELECT id, role FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not op:
        return jsonify({'error': '操作员不存在'}), 404
    
    import_configs = data.get('configs')
    if not import_configs or not isinstance(import_configs, list):
        return jsonify({'error': '导入数据格式错误，应为配置数组'}), 400
    
    conflicts = []
    validation_errors = []
    valid_configs = []
    
    for idx, c_data in enumerate(import_configs):
        errors = []
        if not c_data.get('profile_name'):
            errors.append('配置名称不能为空')
        if not c_data.get('service_host'):
            errors.append('服务地址不能为空')
        port = c_data.get('service_port')
        if not isinstance(port, int) or not (0 < port < 65536):
            errors.append('端口必须是1-65535之间的有效整数')
        if errors:
            validation_errors.append({'index': idx, 'name': c_data.get('profile_name', f'配置{idx}'), 'errors': errors})
            continue
        
        existing = db.execute('SELECT * FROM connection_configs WHERE profile_name = ?',
                             (c_data['profile_name'],)).fetchone()
        if existing:
            enrich_connection_config(db, existing)
            conflicts.append({
                'index': idx,
                'import_name': c_data['profile_name'],
                'local_id': existing['id'],
                'local_profile_name': existing['profile_name'],
                'local_host': existing['service_host'],
                'local_port': existing['service_port'],
                'import_host': c_data.get('service_host'),
                'import_port': c_data.get('service_port'),
                'import_version': c_data.get('config_version'),
                'local_version': existing['config_version'],
                'default_resolution': 'KEEP_LOCAL',
                'local': {
                    'id': existing['id'],
                    'profile_name': existing['profile_name'],
                    'service_host': existing['service_host'],
                    'service_port': existing['service_port'],
                    'entry_path': existing['entry_path'],
                    'protocol': existing['protocol'],
                    'config_version': existing['config_version'],
                    'operator_name': existing.get('operator_name'),
                    'updated_at': existing.get('updated_at'),
                    'created_at': existing.get('created_at')
                },
                'imported': {
                    'profile_name': c_data['profile_name'],
                    'service_host': c_data.get('service_host'),
                    'service_port': c_data.get('service_port'),
                    'entry_path': c_data.get('entry_path', '/'),
                    'protocol': c_data.get('protocol', 'http'),
                    'config_version': c_data.get('config_version', 1),
                    'current_operator_id': c_data.get('current_operator_id')
                }
            })
        
        for other in valid_configs:
            if other['profile_name'] == c_data['profile_name']:
                errors.append(f'导入数据中存在重复的配置名称"{c_data["profile_name"]}"')
                break
        
        if not errors:
            valid_configs.append(c_data)
    
    if validation_errors:
        return jsonify({
            'error': '导入数据校验失败',
            'validation_errors': validation_errors,
            'conflicts': conflicts,
            'valid_count': len(valid_configs)
        }), 400
    
    if conflicts:
        return jsonify({
            'error': '存在配置冲突，请选择解决方式',
            'conflicts': conflicts,
            'valid_count': len(valid_configs),
            'conflict_count': len(conflicts),
            'requires_resolution': True
        }), 409
    
    imported_ids = []
    try:
        for c_data in valid_configs:
            if c_data.get('is_default'):
                db.execute("UPDATE connection_configs SET is_default = 0, updated_at = CURRENT_TIMESTAMP")
            
            cursor = db.execute('''INSERT INTO connection_configs 
                                  (profile_name, service_host, service_port, entry_path, protocol,
                                   is_default, config_version, created_by, updated_by)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                               (c_data['profile_name'],
                                c_data.get('service_host', '127.0.0.1'),
                                int(c_data.get('service_port', 5002)),
                                c_data.get('entry_path', '/') or '/',
                                c_data.get('protocol', 'http') or 'http',
                                1 if c_data.get('is_default') else 0,
                                int(c_data.get('config_version', 1)),
                                operator_id, operator_id))
            config_id = cursor.lastrowid
            imported_ids.append(config_id)
            save_connection_snapshot(db, config_id, operator_id, int(c_data.get('config_version', 1)))
            add_connection_log(db, config_id, c_data['profile_name'], CONNECTION_LOG_ACTION['IMPORT_CONFIG'],
                              operator_id, {'imported_name': c_data['profile_name']})
        
        db.commit()
        result_configs = []
        for sid in imported_ids:
            c = db.execute('SELECT * FROM connection_configs WHERE id = ?', (sid,)).fetchone()
            enrich_connection_config(db, c)
            result_configs.append(c)
        
        return jsonify({
            'success': True,
            'imported_count': len(imported_ids),
            'imported_ids': imported_ids,
            'configs': result_configs
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'导入失败: {str(e)}'}), 500

@app.route('/api/connection/import/resolve', methods=['POST'])
def resolve_connection_conflicts():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    operator_id = data.get('operator_id')
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    resolutions = data.get('resolutions')
    import_configs = data.get('configs')
    if not resolutions or not isinstance(resolutions, dict):
        return jsonify({'error': '缺少冲突解决配置'}), 400
    if not import_configs or not isinstance(import_configs, list):
        return jsonify({'error': '缺少导入配置数据'}), 400
    
    imported_ids = []
    resolution_logs = []
    
    try:
        for idx_str, resolution in resolutions.items():
            idx = int(idx_str)
            if idx >= len(import_configs):
                continue
            c_data = import_configs[idx]
            mode = resolution.get('mode', 'KEEP_LOCAL')
            
            if mode == 'KEEP_LOCAL':
                resolution_logs.append({
                    'index': idx, 'name': c_data.get('profile_name'),
                    'mode': mode, 'result': 'SKIPPED'
                })
                continue
            
            existing = db.execute('SELECT * FROM connection_configs WHERE profile_name = ?',
                                 (c_data['profile_name'],)).fetchone()
            
            if mode == 'OVERWRITE' and existing:
                old_version = existing['config_version']
                new_version = max(old_version, int(c_data.get('config_version', 1))) + 1
                db.execute('''UPDATE connection_configs SET
                              service_host = ?, service_port = ?, entry_path = ?, protocol = ?,
                              config_version = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                              WHERE id = ?''',
                           (c_data.get('service_host', existing['service_host']),
                            int(c_data.get('service_port', existing['service_port'])),
                            c_data.get('entry_path', existing['entry_path']) or '/',
                            c_data.get('protocol', existing['protocol']) or 'http',
                            new_version, operator_id, existing['id']))
                save_connection_snapshot(db, existing['id'], operator_id, new_version)
                add_connection_log(db, existing['id'], c_data['profile_name'],
                                  CONNECTION_LOG_ACTION['RESOLVE_CONFLICT'], operator_id,
                                  {'mode': 'OVERWRITE', 'from_version': old_version, 'to_version': new_version})
                imported_ids.append(existing['id'])
                resolution_logs.append({
                    'index': idx, 'name': c_data.get('profile_name'),
                    'mode': mode, 'result': 'OVERWRITTEN', 'config_id': existing['id']
                })
            
            elif mode == 'SAVE_AS_NEW':
                new_name = resolution.get('new_name', f"{c_data['profile_name']}_导入_{int(time.time())}")
                if c_data.get('is_default'):
                    db.execute("UPDATE connection_configs SET is_default = 0, updated_at = CURRENT_TIMESTAMP")
                cursor = db.execute('''INSERT INTO connection_configs 
                                      (profile_name, service_host, service_port, entry_path, protocol,
                                       is_default, config_version, created_by, updated_by)
                                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                   (new_name,
                                    c_data.get('service_host', '127.0.0.1'),
                                    int(c_data.get('service_port', 5002)),
                                    c_data.get('entry_path', '/') or '/',
                                    c_data.get('protocol', 'http') or 'http',
                                    1 if c_data.get('is_default') else 0,
                                    int(c_data.get('config_version', 1)),
                                    operator_id, operator_id))
                config_id = cursor.lastrowid
                save_connection_snapshot(db, config_id, operator_id, int(c_data.get('config_version', 1)))
                add_connection_log(db, config_id, new_name, CONNECTION_LOG_ACTION['RESOLVE_CONFLICT'],
                                  operator_id, {'mode': 'SAVE_AS_NEW', 'original_name': c_data['profile_name'], 'new_name': new_name})
                imported_ids.append(config_id)
                resolution_logs.append({
                    'index': idx, 'name': c_data.get('profile_name'),
                    'mode': mode, 'new_name': new_name,
                    'result': 'CREATED_AS_NEW', 'config_id': config_id
                })
        
        db.commit()
        result_configs = []
        for sid in imported_ids:
            c = db.execute('SELECT * FROM connection_configs WHERE id = ?', (sid,)).fetchone()
            enrich_connection_config(db, c)
            result_configs.append(c)
        
        return jsonify({
            'success': True,
            'imported_count': len(imported_ids),
            'imported_ids': imported_ids,
            'resolution_logs': resolution_logs,
            'configs': result_configs
        })
    except Exception as e:
        db.rollback()
        return jsonify({'error': f'冲突解决失败: {str(e)}'}), 500

@app.route('/api/connection/configs/<int:config_id>/rollback', methods=['POST'])
def rollback_connection_config(config_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    operator_id = data.get('operator_id')
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    if not config:
        return jsonify({'error': '连接配置不存在'}), 404
    
    target_version = data.get('target_version')
    if target_version is None:
        target_version = config['config_version'] - 1
    if target_version < 1:
        return jsonify({'error': '没有更早的版本可以回退'}), 400
    
    snapshot = db.execute('''SELECT * FROM connection_config_snapshots 
                             WHERE config_id = ? AND version = ? ORDER BY id DESC LIMIT 1''',
                          (config_id, target_version)).fetchone()
    if not snapshot:
        return jsonify({'error': f'找不到版本 {target_version} 的快照'}), 404
    
    try:
        snap_data = json.loads(snapshot['snapshot_data'])
    except Exception as e:
        return jsonify({'error': f'快照数据解析失败: {str(e)}'}), 500
    
    old_version = config['config_version']
    
    db.execute('''UPDATE connection_configs SET
                  profile_name = ?, service_host = ?, service_port = ?, entry_path = ?, protocol = ?,
                  current_operator_id = ?, is_default = ?, config_version = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                  WHERE id = ?''',
               (snap_data.get('profile_name', config['profile_name']),
                snap_data.get('service_host', config['service_host']),
                int(snap_data.get('service_port', config['service_port'])),
                snap_data.get('entry_path', config['entry_path']) or '/',
                snap_data.get('protocol', config['protocol']) or 'http',
                snap_data.get('current_operator_id'),
                snap_data.get('is_default', 0),
                target_version, operator_id, config_id))
    
    save_connection_snapshot(db, config_id, operator_id, target_version)
    add_connection_log(db, config_id, config['profile_name'], CONNECTION_LOG_ACTION['ROLLBACK_CONFIG'],
                      operator_id, {'from_version': old_version, 'to_version': target_version})
    db.commit()
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    enrich_connection_config(db, config)
    return jsonify({
        'success': True,
        'from_version': old_version,
        'to_version': target_version,
        'config': config
    })

@app.route('/api/connection/configs/<int:config_id>/publish', methods=['POST'])
def publish_connection_config(config_id):
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    operator_id = data.get('operator_id')
    if not operator_id:
        return jsonify({'error': '缺少operator_id参数'}), 400
    
    ok, err = check_connection_publish_permission(operator_id, db)
    if not ok:
        return jsonify({'error': err}), 403
    
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    if not config:
        return jsonify({'error': '连接配置不存在'}), 404
    
    if config['is_published']:
        return jsonify({'error': '该配置已经是发布状态'}), 400
    
    db.execute('''UPDATE connection_configs SET
                  is_published = 1, published_by = ?, published_at = CURRENT_TIMESTAMP,
                  updated_at = CURRENT_TIMESTAMP
                  WHERE id = ?''', (operator_id, config_id))
    
    add_connection_log(db, config_id, config['profile_name'], CONNECTION_LOG_ACTION['PUBLISH_CONFIG'],
                      operator_id, {'published_version': config['config_version']})
    db.commit()
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    enrich_connection_config(db, config)
    return jsonify(config)

@app.route('/api/connection/logs', methods=['GET'])
def get_connection_logs():
    db = get_db()
    db.row_factory = dict_factory
    config_id = request.args.get('config_id', type=int)
    query = 'SELECT * FROM connection_logs'
    params = []
    if config_id:
        query += ' WHERE config_id = ?'
        params.append(config_id)
    query += ' ORDER BY created_at DESC LIMIT 100'
    logs = db.execute(query, params).fetchall()
    for log in logs:
        if log.get('detail'):
            try:
                log['detail'] = json.loads(log['detail'])
            except Exception:
                pass
    return jsonify(logs)

@app.route('/api/connection/check-strategy-access', methods=['POST'])
def check_strategy_access():
    data = request.json
    db = get_db()
    db.row_factory = dict_factory
    config_id = data.get('config_id')
    operator_id = data.get('operator_id')
    
    if not config_id or not operator_id:
        return jsonify({
            'has_access': False,
            'blocked': True,
            'reason': '缺少必要参数',
            'required_fields': ['config_id', 'operator_id'],
            'suggestion': '请在连接与诊断中心中选择配置并设置操作人'
        })
    
    config = db.execute('SELECT * FROM connection_configs WHERE id = ?', (config_id,)).fetchone()
    if not config:
        return jsonify({
            'has_access': False,
            'blocked': True,
            'reason': '连接配置不存在',
            'suggestion': '请先创建有效的连接配置'
        })
    
    op = db.execute('SELECT * FROM users WHERE id = ?', (operator_id,)).fetchone()
    if not op:
        return jsonify({
            'has_access': False,
            'blocked': True,
            'reason': '操作人不存在',
            'suggestion': '请选择有效的操作人账号'
        })
    
    if not config['current_operator_id']:
        return jsonify({
            'has_access': False,
            'blocked': True,
            'reason': '当前连接未设置操作人',
            'suggestion': '请在连接与诊断中心中设置当前操作人',
            'missing_context': ['current_operator_id']
        })
    
    if op['role'] not in STRATEGY_PERMISSION_ROLES:
        return jsonify({
            'has_access': False,
            'blocked': True,
            'reason': f'操作人角色为{USER_ROLES.get(op["role"], op["role"])}，无权访问策略中心',
            'required_role': 'RECEIVER（接收方）',
            'suggestion': '请切换到接收方(RECEIVER)角色的账号后再访问策略中心',
            'current_role': op['role'],
            'current_role_name': USER_ROLES.get(op['role'], op['role'])
        })
    
    return jsonify({
        'has_access': True,
        'blocked': False,
        'operator': {
            'id': op['id'],
            'username': op['username'],
            'role': op['role'],
            'role_name': USER_ROLES.get(op['role'], op['role'])
        },
        'config': {
            'id': config['id'],
            'profile_name': config['profile_name'],
            'entry_url': f"{config['protocol']}://{config['service_host']}:{config['service_port']}{config['entry_path']}"
        },
        'can_view_list': True,
        'can_view_version': True,
        'can_view_detail': True,
        'can_export': True
    })

if __name__ == '__main__':
    init_db()
    app.run(host='127.0.0.1', port=5002, debug=True)
