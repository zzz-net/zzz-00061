import requests
import json
import sys

BASE = 'http://127.0.0.1:5002/api'
PASS = 0
FAIL = 0

def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}  {detail}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name}  {detail}')

def hr(title):
    print(f'\n=== {title} ' + '=' * (70 - len(title)))

print('\n' + '#' * 70)
print('#  连接工作台 - 统一数据模型 & 真实请求验证脚本')
print('#' * 70)

hr('1. 获取基础数据（用户、连接配置）')
users = requests.get(f'{BASE}/users').json()
check('GET /users 返回用户列表', len(users) >= 2, f'共 {len(users)} 个用户')
sender = next((u for u in users if u['role'] == 'SENDER'), None)
receiver = next((u for u in users if u['role'] == 'RECEIVER'), None)
check('存在 SENDER 和 RECEIVER 用户', sender and receiver, f'sender={sender and sender["id"]}, receiver={receiver and receiver["id"]}')

cfgs = requests.get(f'{BASE}/connection/configs').json()
check('GET /connection/configs 返回连接配置', len(cfgs) >= 1, f'共 {len(cfgs)} 个配置')
default_cfg = cfgs[0] if cfgs else None
check('存在默认连接配置', default_cfg is not None, f'id={default_cfg and default_cfg["id"]}')

hr('2. 缺参拦截（POST /batches/<id>/export 无 operator_id）')
r_missing = requests.post(f'{BASE}/batches/99999/export', json={})
check('缺 operator_id 返回 400', r_missing.status_code == 400, f'实际 {r_missing.status_code}, body={r_missing.text[:80]}')
err = r_missing.json().get('error', '')
check('错误消息包含 operator_id 提示', 'operator_id' in err or '导出操作失败' in err, f'消息: {err[:80]}')

hr('3. 上下文 GET /context 初始恢复')
ctx0 = requests.get(f'{BASE}/context').json()
check('GET /context 返回结构完整', all(k in ctx0 for k in ['current_operator_id', 'selected_batch_id', 'connection_config_id', 'source', 'available_users']), f'source={ctx0.get("source")}')
check('初始状态有可用操作人', ctx0.get('current_operator_id') is not None, f'operator_id={ctx0.get("current_operator_id")}')
print(f'  info: source={ctx0.get("source")}, message={ctx0.get("message")}')

hr('4. 先登记一个测试批次（确保有可导出的批次）')
register_payload = {
    'batch_no': f'TEST-EXPORT-{int(__import__("time").time())}',
    'description': '统一操作人验证批次',
    'created_by': sender['id'],
    'archives': [{'archive_no': 'A001', 'title': '测试档案001', 'remark': 't'}]
}
r_reg = requests.post(f'{BASE}/batches', json=register_payload)
check('批次登记成功', r_reg.status_code in (200, 201), f'status={r_reg.status_code}')
batch_id = None
if r_reg.ok:
    batch_id = r_reg.json().get('batch_id') or r_reg.json().get('id')
    check('返回批次 ID', batch_id is not None, f'batch_id={batch_id}')

if not batch_id:
    batches = requests.get(f'{BASE}/batches').json()
    if batches:
        batch_id = batches[0]['id']
        print(f'  info: 使用已有批次 id={batch_id}')

hr('5. 导出档案（直接传 operator_id）')
r_exp1 = requests.post(f'{BASE}/batches/{batch_id}/export', json={'operator_id': sender['id']})
check('直接传 operator_id 导出 200', r_exp1.status_code == 200, f'实际 {r_exp1.status_code}')
if r_exp1.ok:
    d1 = r_exp1.json()
    check('返回 exported_by 字段', 'exported_by' in d1, f'exported_by={d1.get("exported_by")}')
    check('exported_by == sender.id', d1.get('exported_by') == sender['id'], f'期望 {sender["id"]}, 实际 {d1.get("exported_by")}')
    check('返回 exported_by_name 字段', d1.get('exported_by_name') == sender['username'], f'期望 {sender["username"]}, 实际 {d1.get("exported_by_name")}')
    check('operator_source == direct_operator', d1.get('operator_source') == 'direct_operator', f'实际 {d1.get("operator_source")}')
    export1_id = d1.get('export_id')
    print(f'  info: export_id={export1_id}, source={d1.get("operator_source")}, by={d1.get("exported_by_name")}')

hr('6. 切换连接配置操作人（POST /switch-operator）')
cfg_id = default_cfg['id']
r_sw = requests.post(f'{BASE}/connection/configs/{cfg_id}/switch-operator',
                     json={'operator_id': receiver['id'], 'updated_by': sender['id']})
check('switch-operator 200', r_sw.status_code == 200, f'实际 {r_sw.status_code}, body={r_sw.text[:120]}')
if r_sw.ok:
    swd = r_sw.json()
    check('返回 current_operator_id == receiver.id', swd.get('current_operator_id') == receiver['id'], f'实际 {swd.get("current_operator_id")}')
    check('返回 current_operator_name == receiver.username', swd.get('current_operator_name') == receiver['username'], f'实际 {swd.get("current_operator_name")}')
    check('返回 version_change 字段', 'version_change' in swd, f'change={swd.get("version_change")}')
    check('返回 can_access_strategies 布尔', isinstance(swd.get('can_access_strategies'), bool), f'receiver 角色 RECEIVER 应有权限={swd.get("can_access_strategies")}')

hr('7. 导出档案通过 connection_config_id 解析操作人')
r_exp2 = requests.post(f'{BASE}/batches/{batch_id}/export', json={'connection_config_id': cfg_id})
check('connection_config_id 方式导出 200', r_exp2.status_code == 200, f'实际 {r_exp2.status_code}, body={r_exp2.text[:120]}')
if r_exp2.ok:
    d2 = r_exp2.json()
    check('通过配置解析的操作人 = receiver', d2.get('exported_by') == receiver['id'], f'期望 {receiver["id"]}, 实际 {d2.get("exported_by")}')
    check('exported_by_name = receiver.username', d2.get('exported_by_name') == receiver['username'], f'实际 {d2.get("exported_by_name")}')
    check('operator_source = connection_config', d2.get('operator_source') == 'connection_config', f'实际 {d2.get("operator_source")}')
    print(f'  info: export_id={d2.get("export_id")}, source={d2.get("operator_source")}, by={d2.get("exported_by_name")}')

hr('8. 连接配置数据库落库验证（切到 receiver 后 current_operator_id 持久化）')
cfg_after = requests.get(f'{BASE}/connection/configs/{cfg_id}').json()
check('GET 配置详情后 current_operator_id == receiver.id',
      cfg_after.get('config', {}).get('current_operator_id') == receiver['id'],
      f'实际 db值={cfg_after.get("config", {}).get("current_operator_id")}')
check('operator_name == receiver.username',
      cfg_after.get('config', {}).get('operator_name') == receiver['username'],
      f'实际={cfg_after.get("config", {}).get("operator_name")}')

hr('9. 上下文 POST /context 保存')
r_ctx_save = requests.post(f'{BASE}/context', json={
    'operator_id': receiver['id'],
    'selected_batch_id': batch_id,
    'connection_config_id': cfg_id
})
check('POST /context 保存成功', r_ctx_save.status_code == 200, f'实际 {r_ctx_save.status_code}, body={r_ctx_save.text[:100]}')
if r_ctx_save.ok:
    s = r_ctx_save.json()
    check('saved_at 字段存在', 'saved_at' in s, f'at={s.get("saved_at")}')
    check('回显 selected_batch_id == batch_id', s.get('selected_batch_id') == batch_id)
    check('回显 connection_config_id == cfg_id', s.get('connection_config_id') == cfg_id)

hr('10. 上下文 GET /context 重启后恢复（验证 SAVE_CONTEXT 被读取）')
ctx1 = requests.get(f'{BASE}/context').json()
check('GET /context source == last_saved', ctx1.get('source') == 'last_saved', f'实际 {ctx1.get("source")}')
check('restored_from_audit == True', ctx1.get('restored_from_audit') is True, f'实际 {ctx1.get("restored_from_audit")}')
check('恢复的 current_operator_id == receiver', ctx1.get('current_operator_id') == receiver['id'], f'实际 {ctx1.get("current_operator_id")}')
check('恢复的 selected_batch_id == batch_id', ctx1.get('selected_batch_id') == batch_id, f'实际 {ctx1.get("selected_batch_id")}')
check('恢复的 connection_config_id == cfg_id', ctx1.get('connection_config_id') == cfg_id, f'实际 {ctx1.get("connection_config_id")}')
print(f'  info: last_saved_at={ctx1.get("last_saved_at")}, msg={ctx1.get("message")}')

hr('11. 审计日志验证（audit_logs 表）')
import sqlite3
db = sqlite3.connect('archive_transfer.db')
db.row_factory = sqlite3.Row
logs = db.execute('SELECT * FROM audit_logs ORDER BY id DESC LIMIT 20').fetchall()
db.close()
log_list = [dict(l) for l in logs]
check('audit_logs 表有记录', len(log_list) > 0, f'共 {len(log_list)} 条')
act_export = [l for l in log_list if l['action'] == 'EXPORT_BATCH']
check('存在 EXPORT_BATCH 审计记录', len(act_export) >= 2, f'实际 {len(act_export)} 条导出记录')
act_switch = [l for l in log_list if l['action'] == 'SWITCH_OPERATOR']
check('存在 SWITCH_OPERATOR 审计记录', len(act_switch) >= 1, f'实际 {len(act_switch)} 条切换记录')
act_savectx = [l for l in log_list if l['action'] == 'SAVE_CONTEXT']
check('存在 SAVE_CONTEXT 审计记录', len(act_savectx) >= 1, f'实际 {len(act_savectx)} 条上下文保存')

if act_export:
    for a in act_export[:2]:
        detail = json.loads(a['detail']) if a.get('detail') else {}
        print(f'  info: EXPORT_BATCH operator={a["operator_id"]}, source={detail.get("source")}, export_id={detail.get("export_id")}')

hr('12. 切换回 sender（保证后续流程干净）并导出 3 次验证稳定性')
requests.post(f'{BASE}/connection/configs/{cfg_id}/switch-operator',
              json={'operator_id': sender['id'], 'updated_by': sender['id']})
print('  info: 切换回 sender，开始稳定性验证...')
stable_ok = True
for i in range(3):
    r = requests.post(f'{BASE}/batches/{batch_id}/export', json={'connection_config_id': cfg_id})
    if not r.ok:
        stable_ok = False
        print(f'  第{i+1}次导出失败: {r.status_code} {r.text[:80]}')
        continue
    d = r.json()
    ok_by = d.get('exported_by') == sender['id']
    ok_src = d.get('operator_source') == 'connection_config'
    if not (ok_by and ok_src):
        stable_ok = False
        print(f'  第{i+1}次导出不一致: exported_by={d.get("exported_by")} (期望 {sender["id"]}), source={d.get("operator_source")}')
    else:
        print(f'  第{i+1}次导出: OK by={d.get("exported_by_name")} source={d.get("operator_source")}')
check('连续 3 次导出操作人稳定一致', stable_ok)

hr('13. 缺参拦截：operator_id 传不存在的用户ID')
r_bad_op = requests.post(f'{BASE}/batches/{batch_id}/export', json={'operator_id': 999999})
check('不存在的 operator_id 返回 400/404', r_bad_op.status_code in (400, 404), f'实际 {r_bad_op.status_code}, body={r_bad_op.text[:100]}')

hr('14. 缺参拦截：connection_config_id 传不存在的ID')
r_bad_cfg = requests.post(f'{BASE}/batches/{batch_id}/export', json={'connection_config_id': 999999})
check('不存在的 connection_config_id 返回 400', r_bad_cfg.status_code == 400, f'实际 {r_bad_cfg.status_code}, body={r_bad_cfg.text[:120]}')

hr('15. connection_logs 表连接中心日志完整性')
import sqlite3
db = sqlite3.connect('archive_transfer.db')
db.row_factory = sqlite3.Row
conn_logs = db.execute('SELECT * FROM connection_logs WHERE action = ? ORDER BY id DESC LIMIT 10',
                       ('切换操作人',)).fetchall()
db.close()
check('connection_logs 中存在切换操作人记录', len(conn_logs) >= 2, f'实际 {len(conn_logs)} 条')
if conn_logs:
    cl = dict(conn_logs[0])
    det = json.loads(cl['detail']) if cl.get('detail') else {}
    print(f'  info: 最近切换 from={det.get("from_operator_name")} to={det.get("to_operator_name")} by={det.get("executed_by")}')

print('\n' + '=' * 70)
print(f'  总计: 通过 {PASS} / 失败 {FAIL}  (共 {PASS + FAIL} 项)')
print('=' * 70)

if FAIL > 0:
    sys.exit(1)
