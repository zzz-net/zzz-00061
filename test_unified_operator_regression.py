"""
统一操作人 - 完整自动化回归测试套件

测试范围：
  链路1：手动切换操作人 (switch-operator)
  链路2：只传 connection_config_id 导出
  链路3：保存上下文 → 重启 → 恢复上下文

核心验证点（最容易被说反的两个点）：
  P1：刷新或恢复时到底会不会自动带查询参数？
      → 不会。GET /context 不带 ?operator_id= 查询参数时，应从审计日志/SQLite 恢复，
         仅当显式传了 ?operator_id= 才用查询参数（source='query_param'）。
  P2：导出记录的 source 和 exported_by 到底跟谁走？
      → exported_by 优先级：显式 operator_id > connection_config.current_operator_id
      → operator_source：传了 connection_config_id 就是 'connection_config'，否则 'direct_operator'
      → 与"当前界面选中的操作人"、"最近保存的上下文"无关，只看本次导出请求实际传的参数。

复跑命令：
  # 先启动服务
  $env:FLASK_APP='app.py' ; python app.py
  # 新开终端运行测试
  python test_unified_operator_regression.py
"""

import requests
import json
import sys
import sqlite3
import time
from datetime import datetime

BASE = 'http://127.0.0.1:5002/api'
DB_PATH = 'archive_transfer.db'

PASS = 0
FAIL = 0
FAIL_DETAILS = []


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}  {detail}')
    else:
        FAIL += 1
        FAIL_DETAILS.append((name, detail))
        print(f'  [FAIL] {name}  {detail}')


def hr(title):
    print(f'\n=== {title} ' + '=' * (70 - len(title)))


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def fetch_audit_logs(limit=30):
    db = get_db()
    rows = db.execute('SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def fetch_export_records(batch_id=None, limit=20):
    db = get_db()
    if batch_id:
        rows = db.execute('''SELECT e.*, u.username as exporter_name
                            FROM export_records e JOIN users u ON e.exported_by = u.id
                            WHERE e.batch_id = ? ORDER BY e.id DESC LIMIT ?''',
                         (batch_id, limit)).fetchall()
    else:
        rows = db.execute('''SELECT e.*, u.username as exporter_name
                            FROM export_records e JOIN users u ON e.exported_by = u.id
                            ORDER BY e.id DESC LIMIT ?''', (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def fetch_connection_config(cfg_id):
    db = get_db()
    row = db.execute('SELECT * FROM connection_configs WHERE id = ?', (cfg_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def assert_audit_record(action, expected_operator_id=None, expected_target_id=None,
                        expected_detail_contains=None, min_count=1):
    logs = fetch_audit_logs(50)
    matched = [l for l in logs if l['action'] == action]
    if expected_operator_id:
        matched = [l for l in matched if l['operator_id'] == expected_operator_id]
    if expected_target_id:
        matched = [l for l in matched if l['target_id'] == expected_target_id]
    if expected_detail_contains:
        filtered = []
        for l in matched:
            try:
                detail = json.loads(l['detail']) if l.get('detail') else {}
            except Exception:
                detail = {}
            all_match = True
            for k, v in expected_detail_contains.items():
                if detail.get(k) != v:
                    all_match = False
                    break
            if all_match:
                filtered.append(l)
        matched = filtered
    return len(matched) >= min_count, matched


def clean_dirty_context():
    """清除脏上下文：删除所有 SAVE_CONTEXT 审计记录"""
    db = get_db()
    db.execute("DELETE FROM audit_logs WHERE action = 'SAVE_CONTEXT'")
    db.commit()
    db.close()


# ============================================================
print('\n' + '#' * 75)
print('#  统一操作人 - 完整自动化回归测试套件')
print('#  三条链路 + 两个核心验证点 + 错误场景 + 稳定性')
print('#' * 75)

# ============================================================
hr('前置准备：获取基础数据并清理脏上下文')

clean_dirty_context()
print('  info: 已清除历史 SAVE_CONTEXT 记录，确保上下文纯净')

users = requests.get(f'{BASE}/users').json()
check('GET /users 返回用户列表', len(users) >= 2, f'共 {len(users)} 个用户')
sender = next((u for u in users if u['role'] == 'SENDER'), None)
receiver = next((u for u in users if u['role'] == 'RECEIVER'), None)
check('存在 SENDER 和 RECEIVER 用户', sender and receiver,
      f'sender={sender and sender["id"]}, receiver={receiver and receiver["id"]}')

cfgs = requests.get(f'{BASE}/connection/configs').json()
check('GET /connection/configs 返回连接配置', len(cfgs) >= 1, f'共 {len(cfgs)} 个配置')
default_cfg = cfgs[0] if cfgs else None
check('存在默认连接配置', default_cfg is not None, f'id={default_cfg and default_cfg["id"]}')
cfg_id = default_cfg['id'] if default_cfg else None

# 登记测试批次
register_payload = {
    'batch_no': f'REG-TEST-{int(time.time())}',
    'description': '统一操作人回归测试批次',
    'created_by': sender['id'],
    'archives': [{'archive_no': 'A001', 'title': '测试档案001', 'remark': 't'}]
}
r_reg = requests.post(f'{BASE}/batches', json=register_payload)
check('批次登记成功', r_reg.status_code in (200, 201), f'status={r_reg.status_code}')
batch_id = None
if r_reg.ok:
    batch_id = r_reg.json().get('batch_id') or r_reg.json().get('id')
    check('返回批次 ID', batch_id is not None, f'batch_id={batch_id}')

# ============================================================
# 链路1：手动切换操作人
# ============================================================
hr('链路1：手动切换操作人 (switch-operator) - 全链路验证')

# 1a: 初始状态 - 连接配置 current_operator_id 应为 None 或某个值
initial_cfg = fetch_connection_config(cfg_id)
initial_op = initial_cfg.get('current_operator_id')
print(f'  info: 初始 current_operator_id = {initial_op}')

# 1b: 切换到 receiver
r_sw1 = requests.post(f'{BASE}/connection/configs/{cfg_id}/switch-operator',
                      json={'operator_id': receiver['id'], 'updated_by': sender['id']})
check('切换操作人 → receiver 返回 200', r_sw1.status_code == 200,
      f'实际 {r_sw1.status_code}, body={r_sw1.text[:120]}')

if r_sw1.ok:
    swd = r_sw1.json()
    check('返回 current_operator_id == receiver.id',
          swd.get('current_operator_id') == receiver['id'],
          f'期望 {receiver["id"]}, 实际 {swd.get("current_operator_id")}')
    check('返回 current_operator_name == receiver.username',
          swd.get('current_operator_name') == receiver['username'],
          f'实际 {swd.get("current_operator_name")}')
    check('返回 version_change 字段', 'version_change' in swd,
          f'change={swd.get("version_change")}')

# 1c: SQLite 落库验证
cfg_after_sw1 = fetch_connection_config(cfg_id)
check('SQLite 落库：current_operator_id == receiver.id',
      cfg_after_sw1.get('current_operator_id') == receiver['id'],
      f'db实际值={cfg_after_sw1.get("current_operator_id")}')
check('SQLite 落库：config_version 递增',
      cfg_after_sw1.get('config_version') == (initial_cfg.get('config_version', 0) + 1),
      f'期望 v{initial_cfg.get("config_version", 0) + 1}, 实际 v{cfg_after_sw1.get("config_version")}')

# 1d: 审计日志验证 (SWITCH_OPERATOR)
found_sw, sw_logs = assert_audit_record(
    'SWITCH_OPERATOR',
    expected_operator_id=sender['id'],
    expected_target_id=cfg_id,
    expected_detail_contains={'to_operator_id': receiver['id']}
)
check('审计日志：存在 SWITCH_OPERATOR 记录', found_sw, f'匹配记录数={len(sw_logs)}')
if sw_logs:
    detail = json.loads(sw_logs[0]['detail']) if sw_logs[0].get('detail') else {}
    check('审计日志 detail: from_operator_id 正确',
          detail.get('from_operator_id') == initial_op,
          f'期望 {initial_op}, 实际 {detail.get("from_operator_id")}')
    check('审计日志 detail: to_operator_name 正确',
          detail.get('to_operator_name') == receiver['username'],
          f'实际 {detail.get("to_operator_name")}')

# ============================================================
# 链路2：只传 connection_config_id 导出
# ============================================================
hr('链路2：只传 connection_config_id 导出 - 全链路验证')

# 2a: 导出（只传 connection_config_id，不传 operator_id）
r_exp_cfg = requests.post(f'{BASE}/batches/{batch_id}/export',
                          json={'connection_config_id': cfg_id})
check('只传 connection_config_id 导出返回 200', r_exp_cfg.status_code == 200,
      f'实际 {r_exp_cfg.status_code}, body={r_exp_cfg.text[:120]}')

export_cfg_id = None
if r_exp_cfg.ok:
    d = r_exp_cfg.json()
    export_cfg_id = d.get('export_id')

    # 核心验证点 P2-1：exported_by 应来自 connection_config.current_operator_id (= receiver)
    check('P2-1: exported_by == connection_config.current_operator_id (receiver)',
          d.get('exported_by') == receiver['id'],
          f'期望 {receiver["id"]}, 实际 {d.get("exported_by")}')
    check('P2-1: exported_by_name == receiver.username',
          d.get('exported_by_name') == receiver['username'],
          f'实际 {d.get("exported_by_name")}')

    # 核心验证点 P2-2：operator_source == 'connection_config'
    check('P2-2: operator_source == connection_config',
          d.get('operator_source') == 'connection_config',
          f'实际 {d.get("operator_source")}')

# 2b: SQLite export_records 表验证
if export_cfg_id:
    exp_records = fetch_export_records(batch_id)
    this_record = next((r for r in exp_records if r['id'] == export_cfg_id), None)
    check('SQLite: export_records 记录存在', this_record is not None,
          f'export_id={export_cfg_id}')
    if this_record:
        check('SQLite: exported_by == receiver.id',
              this_record['exported_by'] == receiver['id'],
              f'期望 {receiver["id"]}, 实际 {this_record["exported_by"]}')
        check('SQLite: exporter_name == receiver.username',
              this_record['exporter_name'] == receiver['username'],
              f'实际 {this_record["exporter_name"]}')

# 2c: 审计日志验证 (EXPORT_BATCH)
if export_cfg_id:
    found_exp, exp_logs = assert_audit_record(
        'EXPORT_BATCH',
        expected_operator_id=receiver['id'],
        expected_target_id=batch_id,
        expected_detail_contains={'export_id': export_cfg_id, 'source': 'connection_config'}
    )
    check('审计日志：存在 EXPORT_BATCH 记录', found_exp, f'匹配记录数={len(exp_logs)}')
    if exp_logs:
        detail = json.loads(exp_logs[0]['detail']) if exp_logs[0].get('detail') else {}
        check('审计日志 detail: source == connection_config',
              detail.get('source') == 'connection_config',
              f'实际 {detail.get("source")}')

# 2d: 对比：直接传 operator_id 导出（验证优先级）
r_exp_direct = requests.post(f'{BASE}/batches/{batch_id}/export',
                             json={'operator_id': sender['id']})
check('直接传 operator_id 导出返回 200', r_exp_direct.status_code == 200)

if r_exp_direct.ok:
    d2 = r_exp_direct.json()
    # 核心验证点 P2-3：显式 operator_id 优先级更高
    check('P2-3: 显式 operator_id 优先级更高 → exported_by == sender.id',
          d2.get('exported_by') == sender['id'],
          f'期望 {sender["id"]}, 实际 {d2.get("exported_by")}')
    check('P2-3: operator_source == direct_operator',
          d2.get('operator_source') == 'direct_operator',
          f'实际 {d2.get("operator_source")}')

# 2e: 优先级验证：同时传 operator_id 和 connection_config_id
# operator_id 优先级应该更高
r_exp_both = requests.post(f'{BASE}/batches/{batch_id}/export',
                           json={'operator_id': sender['id'], 'connection_config_id': cfg_id})
check('同时传两个参数导出返回 200', r_exp_both.status_code == 200)

if r_exp_both.ok:
    d3 = r_exp_both.json()
    check('P2-4: operator_id 优先级高于 connection_config_id → exported_by == sender',
          d3.get('exported_by') == sender['id'],
          f'期望 {sender["id"]}, 实际 {d3.get("exported_by")}')
    # source 仍为 connection_config（因为传了 connection_config_id 参数）
    check('P2-4: 传了 connection_config_id → operator_source == connection_config',
          d3.get('operator_source') == 'connection_config',
          f'实际 {d3.get("operator_source")}')

# ============================================================
# 链路3：保存上下文 → 重启 → 恢复上下文
# ============================================================
hr('链路3：保存上下文 → 重启 → 恢复上下文 - 全链路验证')

# 3a: GET /context 初始状态（无保存的上下文）
clean_dirty_context()
ctx_initial = requests.get(f'{BASE}/context').json()
check('GET /context 无保存上下文时 source != last_saved',
      ctx_initial.get('source') != 'last_saved',
      f'source={ctx_initial.get("source")}')
check('GET /context restored_from_audit == False',
      ctx_initial.get('restored_from_audit') is not True,
      f'实际 {ctx_initial.get("restored_from_audit")}')

# 3b: POST /context 保存上下文（receiver 操作人 + 当前 batch + 当前 cfg）
r_ctx_save = requests.post(f'{BASE}/context', json={
    'operator_id': receiver['id'],
    'selected_batch_id': batch_id,
    'connection_config_id': cfg_id
})
check('POST /context 保存成功', r_ctx_save.status_code == 200,
      f'实际 {r_ctx_save.status_code}, body={r_ctx_save.text[:100]}')

if r_ctx_save.ok:
    s = r_ctx_save.json()
    check('回显 saved_at 字段', 'saved_at' in s, f'at={s.get("saved_at")}')
    check('回显 selected_batch_id == batch_id', s.get('selected_batch_id') == batch_id)
    check('回显 connection_config_id == cfg_id', s.get('connection_config_id') == cfg_id)

# 3c: 审计日志验证 (SAVE_CONTEXT)
found_save, save_logs = assert_audit_record(
    'SAVE_CONTEXT',
    expected_operator_id=receiver['id'],
    expected_detail_contains={'selected_batch_id': batch_id, 'connection_config_id': cfg_id}
)
check('审计日志：存在 SAVE_CONTEXT 记录', found_save, f'匹配记录数={len(save_logs)}')

# ============================================================
# 核心验证点 P1：刷新或恢复时到底会不会自动带查询参数
# ============================================================
hr('核心验证点 P1：刷新/恢复时会不会自动带查询参数？')

# P1-1: 不带查询参数 GET /context → 应从审计日志恢复
ctx_restored = requests.get(f'{BASE}/context').json()
check('P1-1: 不带查询参数 → source == last_saved',
      ctx_restored.get('source') == 'last_saved',
      f'实际 {ctx_restored.get("source")}')
check('P1-1: restored_from_audit == True',
      ctx_restored.get('restored_from_audit') is True,
      f'实际 {ctx_restored.get("restored_from_audit")}')
check('P1-1: 恢复的 current_operator_id == receiver',
      ctx_restored.get('current_operator_id') == receiver['id'],
      f'实际 {ctx_restored.get("current_operator_id")}')
check('P1-1: 恢复的 selected_batch_id == batch_id',
      ctx_restored.get('selected_batch_id') == batch_id,
      f'实际 {ctx_restored.get("selected_batch_id")}')
check('P1-1: 恢复的 connection_config_id == cfg_id',
      ctx_restored.get('connection_config_id') == cfg_id,
      f'实际 {ctx_restored.get("connection_config_id")}')

# P1-2: 带查询参数 ?operator_id=sender.id GET /context → 应使用查询参数
ctx_query = requests.get(f'{BASE}/context', params={'operator_id': sender['id']}).json()
check('P1-2: 带 ?operator_id= 查询参数 → source == query_param',
      ctx_query.get('source') == 'query_param',
      f'实际 {ctx_query.get("source")}')
check('P1-2: current_operator_id == sender.id (查询参数优先级更高)',
      ctx_query.get('current_operator_id') == sender['id'],
      f'实际 {ctx_query.get("current_operator_id")}')
check('P1-2: restored_from_audit == False (因为用了查询参数)',
      ctx_query.get('restored_from_audit') is False,
      f'实际 {ctx_query.get("restored_from_audit")}')

# P1-3: 查询参数不影响保存的上下文（再次不带参数调用，应仍然是 receiver）
ctx_restored2 = requests.get(f'{BASE}/context').json()
check('P1-3: 查询参数不影响持久化上下文 → 不带参仍恢复为 receiver',
      ctx_restored2.get('current_operator_id') == receiver['id'],
      f'实际 {ctx_restored2.get("current_operator_id")}')
check('P1-3: source 仍为 last_saved',
      ctx_restored2.get('source') == 'last_saved',
      f'实际 {ctx_restored2.get("source")}')

# ============================================================
# 核心验证点 P2：导出记录的 source 和 exported_by 到底跟谁走？
# ============================================================
hr('核心验证点 P2：导出记录 source/exported_by 跟谁走？(优先级验证)')

print('  预期优先级：operator_id(显式) > connection_config.current_operator_id')
print('  预期 source：传了 connection_config_id → connection_config，否则 → direct_operator')
print('  注意：与"当前界面选中的操作人"、"最近保存的上下文"无关！')

# 先把连接配置切回 sender
requests.post(f'{BASE}/connection/configs/{cfg_id}/switch-operator',
              json={'operator_id': sender['id'], 'updated_by': sender['id']})

# P2-5: 当前上下文保存的是 receiver，但连接配置 current_operator 是 sender
# 导出只传 connection_config_id → exported_by 应该是 sender（来自连接配置，不是上下文）
r_exp_p2_5 = requests.post(f'{BASE}/batches/{batch_id}/export',
                           json={'connection_config_id': cfg_id})
check('P2-5: 上下文存的是 receiver，但连接配置是 sender → 只传 cfg_id 导出',
      r_exp_p2_5.status_code == 200)
if r_exp_p2_5.ok:
    d = r_exp_p2_5.json()
    check('P2-5: exported_by == sender (跟随连接配置，不是上下文)',
          d.get('exported_by') == sender['id'],
          f'期望 {sender["id"]}, 实际 {d.get("exported_by")}')
    check('P2-5: operator_source == connection_config',
          d.get('operator_source') == 'connection_config',
          f'实际 {d.get("operator_source")}')

# P2-6: 显式传 operator_id=receiver，覆盖连接配置
r_exp_p2_6 = requests.post(f'{BASE}/batches/{batch_id}/export',
                           json={'operator_id': receiver['id'], 'connection_config_id': cfg_id})
check('P2-6: 同时传 operator_id=receiver + connection_config_id',
      r_exp_p2_6.status_code == 200)
if r_exp_p2_6.ok:
    d = r_exp_p2_6.json()
    check('P2-6: exported_by == receiver (operator_id 优先级更高)',
          d.get('exported_by') == receiver['id'],
          f'期望 {receiver["id"]}, 实际 {d.get("exported_by")}')
    check('P2-6: operator_source == connection_config (因传了 cfg_id)',
          d.get('operator_source') == 'connection_config',
          f'实际 {d.get("operator_source")}')

# ============================================================
# 错误场景：缺参、脏上下文
# ============================================================
hr('错误场景：缺参拦截 & 脏上下文处理')

# 4a: 导出缺参
r_missing = requests.post(f'{BASE}/batches/{batch_id}/export', json={})
check('导出无任何参数 → 400', r_missing.status_code == 400,
      f'实际 {r_missing.status_code}, body={r_missing.text[:80]}')
err = r_missing.json().get('error', '')
check('错误消息包含参数提示', 'operator_id' in err or 'connection_config_id' in err,
      f'消息: {err[:80]}')

# 4b: operator_id 格式错误
r_bad_op = requests.post(f'{BASE}/batches/{batch_id}/export', json={'operator_id': 'not-a-number'})
check('operator_id 非数字 → 400', r_bad_op.status_code == 400)

# 4c: operator_id 不存在
r_bad_op2 = requests.post(f'{BASE}/batches/{batch_id}/export', json={'operator_id': 999999})
check('operator_id 不存在 → 400/404', r_bad_op2.status_code in (400, 404),
      f'实际 {r_bad_op2.status_code}')

# 4d: connection_config_id 不存在
r_bad_cfg = requests.post(f'{BASE}/batches/{batch_id}/export', json={'connection_config_id': 999999})
check('connection_config_id 不存在 → 400', r_bad_cfg.status_code == 400,
      f'实际 {r_bad_cfg.status_code}')

# 4e: connection_config_id 格式错误
r_bad_cfg2 = requests.post(f'{BASE}/batches/{batch_id}/export', json={'connection_config_id': 'bad'})
check('connection_config_id 非数字 → 400', r_bad_cfg2.status_code == 400)

# 4f: 连接配置尚未设置操作人（新建一个配置，不设 operator，尝试导出）
# 先新建一个连接配置
new_cfg_payload = {
    'profile_name': f'测试空操作人配置-{int(time.time())}',
    'service_host': '10.0.0.99',
    'service_port': 8080,
    'entry_path': '/api',
    'protocol': 'http',
    'operator_id': sender['id']
}
r_new_cfg = requests.post(f'{BASE}/connection/configs', json=new_cfg_payload)
new_cfg_id = None
if r_new_cfg.status_code in (200, 201):
    new_cfg_id = r_new_cfg.json().get('id') or r_new_cfg.json().get('config', {}).get('id')
    check('新建空操作人配置成功', new_cfg_id is not None, f'new_cfg_id={new_cfg_id}')

    if new_cfg_id:
        # 新配置 current_operator_id 应为 NULL，导出应失败
        r_exp_empty = requests.post(f'{BASE}/batches/{batch_id}/export',
                                    json={'connection_config_id': new_cfg_id})
        check('4f: 连接配置未设置操作人 → 400', r_exp_empty.status_code == 400,
              f'实际 {r_exp_empty.status_code}, body={r_exp_empty.text[:120]}')
        err_empty = r_exp_empty.json().get('error', '')
        check('错误消息提示需先切换操作人', '尚未设置当前操作人' in err_empty or '请先在连接中心切换操作人' in err_empty,
              f'消息: {err_empty[:100]}')

# 4g: 脏上下文（detail 是非法 JSON）
db = get_db()
db.execute('''INSERT INTO audit_logs (action, target_type, target_id, operator_id, detail)
              VALUES (?, ?, ?, ?, ?)''',
           ('SAVE_CONTEXT', 'APP', 0, sender['id'], '{invalid json !!!'))
db.commit()
db.close()
ctx_dirty = requests.get(f'{BASE}/context').json()
check('4g: 脏上下文 (detail 非法JSON) 不崩溃 → 200',
      isinstance(ctx_dirty, dict) and 'current_operator_id' in ctx_dirty,
      f'返回结构正常')
# 清理脏记录
db = get_db()
db.execute("DELETE FROM audit_logs WHERE detail = '{invalid json !!!'")
db.commit()
db.close()

# 4h: 保存上下文缺参
r_ctx_bad = requests.post(f'{BASE}/context', json={})
check('4h: 保存上下文缺 operator_id → 400', r_ctx_bad.status_code == 400)

# 4i: 保存上下文 selected_batch_id 不存在
r_ctx_bad2 = requests.post(f'{BASE}/context', json={
    'operator_id': sender['id'],
    'selected_batch_id': 999999
})
check('4i: selected_batch_id 不存在 → 404', r_ctx_bad2.status_code == 404)

# 4j: 保存上下文 connection_config_id 不存在
r_ctx_bad3 = requests.post(f'{BASE}/context', json={
    'operator_id': sender['id'],
    'connection_config_id': 999999
})
check('4j: connection_config_id 不存在 → 404', r_ctx_bad3.status_code == 404)

# ============================================================
# 稳定性：连续切换后重复导出不漂移
# ============================================================
hr('稳定性：连续切换操作人后重复导出不漂移')

# 5a: 连续切换 5 次，交替 sender/receiver
ops = [sender, receiver, sender, receiver, sender]
switch_results = []
for i, op in enumerate(ops):
    r = requests.post(f'{BASE}/connection/configs/{cfg_id}/switch-operator',
                      json={'operator_id': op['id'], 'updated_by': sender['id']})
    ok = r.status_code == 200
    switch_results.append((i, op['id'], ok))
    if not ok:
        print(f'  第{i+1}次切换失败: {r.status_code} {r.text[:80]}')

check('连续 5 次切换操作人全部成功', all(ok for _, _, ok in switch_results),
      f'结果: {[(i+1, ok) for i, _, ok in switch_results]}')

# 5b: 验证最后一次切换后的数据库状态
cfg_final = fetch_connection_config(cfg_id)
check('最后一次切换后 current_operator_id == sender.id',
      cfg_final.get('current_operator_id') == sender['id'],
      f'期望 {sender["id"]}, 实际 {cfg_final.get("current_operator_id")}')

# 5c: 连续导出 5 次，操作人不漂移
export_results = []
stable = True
for i in range(5):
    r = requests.post(f'{BASE}/batches/{batch_id}/export',
                      json={'connection_config_id': cfg_id})
    if not r.ok:
        stable = False
        export_results.append((i+1, 'FAIL', r.status_code, None))
        continue
    d = r.json()
    ok_by = d.get('exported_by') == sender['id']
    ok_src = d.get('operator_source') == 'connection_config'
    if not (ok_by and ok_src):
        stable = False
    export_results.append((i+1, 'OK' if ok_by and ok_src else 'DRIFT',
                           d.get('exported_by'), d.get('operator_source')))

for i, status, op_id, src in export_results:
    print(f'  第{i}次导出: {status}  exported_by={op_id}  source={src}')

check('连续 5 次导出操作人稳定不漂移', stable,
      f'漂移检查: {[(i, s) for i, s, _, _ in export_results]}')

# 5d: SQLite 导出记录验证（5 条记录的 exported_by 都是 sender）
all_records = fetch_export_records(batch_id)
last_5 = all_records[:5]  # 最新的 5 条
check('SQLite: 最近 5 条导出记录 exported_by 都是 sender.id',
      len(last_5) >= 5 and all(r['exported_by'] == sender['id'] for r in last_5),
      f'实际 exported_by 列表: {[r["exported_by"] for r in last_5]}')

# ============================================================
# 审计日志完整链路串连验证
# ============================================================
hr('全链路串连：前端参数 → 后端解析 → SQLite 落库 → 审计日志')

all_logs = fetch_audit_logs(100)
print(f'  info: 本次测试共产生 {len(all_logs)} 条审计日志')

# 统计各类操作
action_counts = {}
for l in all_logs:
    action_counts[l['action']] = action_counts.get(l['action'], 0) + 1
print(f'  info: 审计日志类型统计: {action_counts}')

check('存在 SWITCH_OPERATOR 审计记录', action_counts.get('SWITCH_OPERATOR', 0) >= 6,
      f'期望>=6, 实际 {action_counts.get("SWITCH_OPERATOR", 0)}')
check('存在 EXPORT_BATCH 审计记录', action_counts.get('EXPORT_BATCH', 0) >= 10,
      f'期望>=10, 实际 {action_counts.get("EXPORT_BATCH", 0)}')
check('存在 SAVE_CONTEXT 审计记录', action_counts.get('SAVE_CONTEXT', 0) >= 1,
      f'期望>=1, 实际 {action_counts.get("SAVE_CONTEXT", 0)}')

# 验证 EXPORT_BATCH 审计日志与 export_records 表一一对应
exp_records = fetch_export_records(batch_id)
export_logs = [l for l in all_logs if l['action'] == 'EXPORT_BATCH' and l['target_id'] == batch_id]
check('审计日志 EXPORT_BATCH 数量 >= export_records 数量',
      len(export_logs) >= len(exp_records),
      f'export_logs={len(export_logs)}, export_records={len(exp_records)}')

# 每条导出记录都能在审计日志中找到对应
for rec in exp_records[:5]:  # 抽验前 5 条
    found, matched = assert_audit_record(
        'EXPORT_BATCH',
        expected_operator_id=rec['exported_by'],
        expected_target_id=batch_id,
        expected_detail_contains={'export_id': rec['id']}
    )
    check(f'审计日志与导出记录关联验证 export_id={rec["id"]}', found,
          f'exported_by={rec["exported_by"]}({rec["exporter_name"]})')

# ============================================================
# 总结
# ============================================================
print('\n' + '=' * 75)
print(f'  总计: 通过 {PASS} / 失败 {FAIL}  (共 {PASS + FAIL} 项)')
print('=' * 75)

if FAIL > 0:
    print('\n  失败用例详情:')
    for name, detail in FAIL_DETAILS:
        print(f'    - {name}: {detail}')
    print()
    sys.exit(1)
else:
    print('\n  [OK] 所有测试用例通过！')
    print()
    print('  复跑命令:')
    print('    # 1. 启动服务 (如未启动)')
    print('    cd d:\\workSpace\\AI__SPACE\\zzz-00061')
    print("    $env:FLASK_APP='app.py' ; python app.py")
    print('    # 2. 新开终端运行测试')
    print('    cd d:\\workSpace\\AI__SPACE\\zzz-00061')
    print('    python test_unified_operator_regression.py')
    print()
    sys.exit(0)
