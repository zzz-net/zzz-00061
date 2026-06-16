import requests
import sqlite3
import time
import os
from datetime import datetime

API = 'http://127.0.0.1:5000/api'
DB_PATH = 'archive_transfer.db'

PASS = '[OK]'
FAIL = '[FAIL]'
total = 0
passed = 0

def assert_true(cond, msg):
    global total, passed
    total += 1
    if cond:
        passed += 1
        print(f'{PASS} {msg}')
        return True
    else:
        print(f'{FAIL} {msg}')
        return False

def assert_equal(a, b, msg):
    global total, passed
    total += 1
    if a == b:
        passed += 1
        print(f'{PASS} {msg}')
        return True
    else:
        print(f'{FAIL} {msg} (期望={b}, 实际={a})')
        return False

def header(title):
    print()
    print('=' * 60)
    print(f'测试: {title}')
    print('=' * 60)

def create_batch(sender_id, batch_no_suffix, archive_prefix):
    batch_no = f'EXP-{batch_no_suffix}-{int(time.time()%100000)}'
    r = requests.post(f'{API}/batches', json={
        'batch_no': batch_no,
        'description': f'导出测试批次 {batch_no}',
        'created_by': sender_id,
        'archives': [
            {'archive_no': f'{archive_prefix}-1', 'title': f'{archive_prefix} 档案1', 'remark': f'{archive_prefix} 备注'},
            {'archive_no': f'{archive_prefix}-2', 'title': f'{archive_prefix} 档案2', 'remark': ''}
        ]
    })
    return r.json()['id'], batch_no

def full_flow_to_signed(sender_id, receiver_id, batch_id):
    r = requests.post(f'{API}/boxes', json={
        'box_no': f'BOX-EXP-{batch_id}-{int(time.time()%100000)}',
        'batch_id': batch_id,
        'operator_id': sender_id
    })
    detail = requests.get(f'{API}/batches/{batch_id}').json()
    bids = [a['id'] for a in detail['archives']]
    box_id = detail['boxes'][0]['id']
    requests.post(f'{API}/boxes/pack', json={
        'batch_id': batch_id, 'box_id': box_id,
        'archive_ids': bids, 'operator_id': sender_id
    })
    requests.post(f'{API}/batches/{batch_id}/transfer', json={'operator_id': sender_id})
    requests.post(f'{API}/boxes/{box_id}/sign', json={'operator_id': receiver_id})
    return box_id

def main():
    print(f'档案移交流转台 - 导出记录重新下载 串批次 回归测试')
    print(f'测试时间: {datetime.now()}')

    users = requests.get(f'{API}/users').json()
    sender = next(u for u in users if u['role'] == 'SENDER')
    receiver = next(u for u in users if u['role'] == 'RECEIVER')
    print(f'发送方: {sender["username"]} (id={sender["id"]})')
    print(f'接收方: {receiver["username"]} (id={receiver["id"]})')

    # ============ 测试11：两个批次，分别导出，按导出记录id重新下载对应内容 ============
    header('11. 两批次分别导出 - 重新下载不串批次（核心回归）')

    bid_a, bno_a = create_batch(sender['id'], 'A', 'AAA')
    bid_b, bno_b = create_batch(sender['id'], 'B', 'BBB')
    print(f'  批次A: {bno_a} (batch_id={bid_a})')
    print(f'  批次B: {bno_b} (batch_id={bid_b})')

    full_flow_to_signed(sender['id'], receiver['id'], bid_a)
    full_flow_to_signed(sender['id'], receiver['id'], bid_b)

    detail_a = requests.get(f'{API}/batches/{bid_a}').json()
    detail_b = requests.get(f'{API}/batches/{bid_b}').json()
    assert_equal(detail_a['batch']['status'], 'SIGNED', f'批次A {bno_a} 已签收')
    assert_equal(detail_b['batch']['status'], 'SIGNED', f'批次B {bno_b} 已签收')

    # 批次A导出两次，批次B导出一次
    r = requests.post(f'{API}/batches/{bid_a}/export', json={'operator_id': sender['id']})
    exp_a1 = r.json()
    eid_a1 = exp_a1['export_id']
    assert_true(r.status_code == 200 and eid_a1, f'批次A第1次导出成功 (export_id={eid_a1})')

    r = requests.post(f'{API}/batches/{bid_a}/export', json={'operator_id': sender['id']})
    exp_a2 = r.json()
    eid_a2 = exp_a2['export_id']
    assert_true(r.status_code == 200 and eid_a2, f'批次A第2次导出成功 (export_id={eid_a2})')

    r = requests.post(f'{API}/batches/{bid_b}/export', json={'operator_id': receiver['id']})
    exp_b1 = r.json()
    eid_b1 = exp_b1['export_id']
    assert_true(r.status_code == 200 and eid_b1, f'批次B第1次导出成功 (export_id={eid_b1})')

    # 检查导出记录列表接口
    recs_a = requests.get(f'{API}/export-records/{bid_a}').json()
    assert_equal(len(recs_a), 2, f'批次A导出记录列表返回2条')
    assert_equal(recs_a[0]['batch_id'], bid_a, '列表第1条 batch_id 正确')
    assert_equal(recs_a[1]['batch_id'], bid_a, '列表第2条 batch_id 正确')

    recs_b = requests.get(f'{API}/export-records/{bid_b}').json()
    assert_equal(len(recs_b), 1, f'批次B导出记录列表返回1条')
    assert_equal(recs_b[0]['batch_id'], bid_b, '批次B列表 batch_id 正确')

    # 用新的下载接口：按 export_record.id 重新下载，校验内容匹配
    def download_and_check(export_id, expect_batch_no, expect_archive_prefix, label):
        r = requests.get(f'{API}/export-records/{export_id}/download')
        assert_equal(r.status_code, 200, f'{label} 下载状态码 200 (export_id={export_id})')
        content = r.content.decode('utf-8-sig')
        assert_true(expect_batch_no in content,
                    f'{label} 下载内容包含正确批次号 {expect_batch_no} (export_id={export_id})')
        assert_true(f'{expect_archive_prefix}-1' in content and f'{expect_archive_prefix}-2' in content,
                    f'{label} 下载内容包含正确档号 {expect_archive_prefix}-1/2 (export_id={export_id})')
        return content

    c_a1 = download_and_check(eid_a1, bno_a, 'AAA', '批次A第1条记录')
    c_a2 = download_and_check(eid_a2, bno_a, 'AAA', '批次A第2条记录')
    c_b1 = download_and_check(eid_b1, bno_b, 'BBB', '批次B第1条记录')

    # 互斥检查：A的下载结果里绝对不能出现B的批次号，反之亦然
    assert_true(bno_b not in c_a1 and 'BBB' not in c_a1,
                '批次A第1条下载内容不含批次B的信息（防串批次）')
    assert_true(bno_b not in c_a2 and 'BBB' not in c_a2,
                '批次A第2条下载内容不含批次B的信息（防串批次）')
    assert_true(bno_a not in c_b1 and 'AAA' not in c_b1,
                '批次B下载内容不含批次A的信息（防串批次）')

    # 主批次导出路径仍然正常（不破坏原路径）
    r = requests.get(f'{API}/batches/{bid_a}/export')
    assert_equal(r.status_code, 200, '主批次 GET /batches/id/export 仍正常返回 200')
    main_content = r.content.decode('utf-8-sig')
    assert_true(bno_a in main_content, '主批次 GET 下载内容包含正确批次号')
    assert_true(bno_b not in main_content, '主批次 GET 下载内容不含其他批次号')

    print(f'{PASS} 11. 两批次分别导出 - 重新下载不串批次（核心回归） - 通过')

    # ============ 测试12：刷新/重启后记录关联不漂移 ============
    header('12. 数据持久化 - 重启后导出记录与批次关联不漂移')
    assert_true(os.path.exists(DB_PATH), f'数据库文件存在: {DB_PATH}')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('SELECT id, batch_id, file_name FROM export_records WHERE id IN (?, ?, ?) ORDER BY id',
              (eid_a1, eid_a2, eid_b1))
    rows = c.fetchall()
    assert_equal(len(rows), 3, '3条导出记录都持久化在数据库')

    row_a1 = next(r for r in rows if r[0] == eid_a1)
    row_a2 = next(r for r in rows if r[0] == eid_a2)
    row_b1 = next(r for r in rows if r[0] == eid_b1)
    assert_equal(row_a1[1], bid_a, f'记录 {eid_a1} 的 batch_id={bid_a} 正确持久化')
    assert_equal(row_a2[1], bid_a, f'记录 {eid_a2} 的 batch_id={bid_a} 正确持久化')
    assert_equal(row_b1[1], bid_b, f'记录 {eid_b1} 的 batch_id={bid_b} 正确持久化')

    c.execute('SELECT content FROM export_records WHERE id = ?', (eid_b1,))
    saved_content = c.fetchone()[0]
    assert_true(bno_b in saved_content, '持久化的 content 列包含批次B的批次号（重启后仍能正确下载）')
    assert_true('BBB-1' in saved_content and 'BBB-2' in saved_content,
                '持久化的 content 列包含批次B的档号')

    conn.close()
    print(f'{PASS} 12. 数据持久化 - 重启后导出记录与批次关联不漂移 - 通过')

    # ============ 测试13：404 保护 - 不存在的导出记录id ============
    header('13. 边界 - 不存在的 export_id 返回 404')
    r = requests.get(f'{API}/export-records/999999/download')
    assert_equal(r.status_code, 404, '不存在的导出记录返回 404')
    assert_true('error' in r.json(), '404 响应包含 error 字段')
    print(f'{PASS} 13. 边界 - 不存在的 export_id 返回 404 - 通过')

    # ============ 汇总 ============
    print()
    print('=' * 60)
    print(f'测试结果: {passed}/{total} 项通过')
    if passed == total:
        print(f'{PASS} 所有测试通过！')
    else:
        print(f'{FAIL} {total - passed} 项失败！')
    print('=' * 60)

    return passed == total

if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
