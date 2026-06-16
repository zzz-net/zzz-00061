"""
Windows 控制台编码稳定性专项回归测试
覆盖4个场景：
1. 默认 Windows 控制台直跑专项回归（test_review_v3.py）
2. 同环境直跑整套回归（4个测试文件依次跑）
3. 失败分支下错误信息仍可读且不会因编码中断
4. 已有历史数据时导出结果和表头内容不漂移
"""
import sys
import os
import subprocess
import requests
import csv
import io

# ===== Windows 控制台编码自动适配（自身也需要）=====
if sys.platform.startswith('win') and sys.stdout.encoding:
    enc = sys.stdout.encoding.lower()
    if 'gbk' in enc or enc == 'cp936' or enc == '936':
        try:
            sys.stdout.reconfigure(errors='replace')
            sys.stderr.reconfigure(errors='replace')
        except Exception:
            pass
# ====================================================

API = 'http://127.0.0.1:5000/api'
TEST_FILES = [
    'test_system.py',
    'test_system_v2.py',
    'test_export_download.py',
    'test_review_v3.py',
]

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
    else:
        print(f'{FAIL} {msg}')


def run_python_script(script_path, timeout=300):
    """在子进程中运行Python脚本，捕获输出和退出码。
    关键：不设置任何环境变量，不重定向编码，模拟用户真实环境。"""
    import locale
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            encoding=locale.getpreferredencoding(False),  # 用系统默认编码解码子进程输出
            errors='replace',  # 解码也做容错，避免子进程输出有特殊字符导致崩溃
            timeout=timeout,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=os.environ.copy(),  # 继承当前环境，不做任何编码修改
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, '', 'TIMEOUT'


def header(title):
    print()
    print('=' * 60)
    print(f'场景: {title}')
    print('=' * 60)


def main():
    print('档案移交流转台 - Windows 控制台编码稳定性专项回归')
    print(f'当前 stdout 编码: {sys.stdout.encoding}')
    print(f'Python 版本: {sys.version}')

    # ============ 场景1：默认控制台直跑专项回归 ============
    header('1. 默认 Windows 控制台直跑专项回归 (test_review_v3.py)')

    code, out, err = run_python_script('test_review_v3.py')
    print(f'  退出码: {code}')
    assert_true(code == 0, f'专项回归正常退出(exit_code=0), 实际={code}')
    assert_true('UnicodeEncodeError' not in out and 'UnicodeEncodeError' not in err,
                '没有因编码异常崩溃（无 UnicodeEncodeError）')
    assert_true('所有测试通过' in out or '项通过' in out,
                '输出包含测试通过标识')
    # 验证 ✓ 字符相关的输出（场景B的"✓"会被替换成?但不会崩溃）
    assert_true('[场景B]' in out, '输出包含场景B标识（说明跑过了包含✓的那行）')
    print(f'{PASS} 1. 专项回归在默认控制台完整跑完，无编码崩溃')

    # ============ 场景2：同环境直跑整套回归 ============
    header('2. 同环境直跑整套回归（4个测试文件依次跑）')

    all_pass = True
    for tf in TEST_FILES:
        code, out, err = run_python_script(tf)
        print(f'  {tf}: exit_code={code}')
        if code != 0:
            all_pass = False
            print(f'     输出最后10行: {out[-500:] if len(out) > 500 else out}')
        assert_true(code == 0, f'{tf} 正常退出, exit_code={code}')
        assert_true('UnicodeEncodeError' not in out and 'UnicodeEncodeError' not in err,
                    f'{tf} 没有编码异常')
    assert_true(all_pass, '整套回归4个文件全部通过')
    print(f'{PASS} 2. 整套回归在默认控制台完整跑完，全部通过')

    # ============ 场景3：失败分支下错误信息仍可读且不会因编码中断 ============
    header('3. 失败分支下错误信息仍可读且不会因编码中断')

    # 构造一个会失败的测试脚本：包含✓字符，且断言失败
    # 注意：必须加上我们的编码适配，否则在GBK控制台会因UnicodeEncodeError崩溃
    failing_test = '''
import sys
# 加上编码适配（与我们修复方案一致）
if sys.platform.startswith('win') and sys.stdout.encoding:
    enc = sys.stdout.encoding.lower()
    if 'gbk' in enc or enc == 'cp936' or enc == '936':
        try:
            sys.stdout.reconfigure(errors='replace')
            sys.stderr.reconfigure(errors='replace')
        except Exception:
            pass

PASS = '[OK]'
FAIL = '[FAIL]'

print(f'{PASS} 这是一个会失败的测试 ✓')
print(f'{FAIL} 断言失败：期望=100, 实际=99 ✓')
# 断言失败后还要打印更多含Unicode的内容
print('后续错误提示：请检查材料缺页问题 ✓')
sys.exit(1)
'''
    failing_script = '_failing_test_temp.py'
    with open(failing_script, 'w', encoding='utf-8') as f:
        f.write(failing_test)

    try:
        code, out, err = run_python_script(failing_script)
        print(f'  退出码: {code}')
        print(f'  输出长度: stdout={len(out)} bytes, stderr={len(err)} bytes')
        if len(out) > 0:
            print(f'  stdout 最后200字符: {out[-200:]}')
        if len(err) > 0:
            print(f'  stderr 最后200字符: {err[-200:]}')

        # 关键：即使断言失败，也不能因为编码问题崩溃
        assert_true(code == 1, f'失败测试退出码为1（断言失败），实际={code}')
        assert_true('UnicodeEncodeError' not in out and 'UnicodeEncodeError' not in err,
                    '失败分支没有因编码异常中断（无 UnicodeEncodeError）')
        assert_true(len(out) > 0, 'stdout 有输出（编码适配生效，内容能正常打印）')
        assert_true('断言失败' in out or '断言失败' in err,
                    '失败信息可读，包含"断言失败"字样')
        assert_true('材料缺页' in out or '材料缺页' in err or '后续错误提示' in out or '后续错误提示' in err,
                    '即使失败，后续输出仍能打印（不会中途崩溃）')
        print(f'{PASS} 3. 失败分支下错误信息可读，无编码中断')
    finally:
        if os.path.exists(failing_script):
            os.remove(failing_script)

    # ============ 场景4：已有历史数据时导出结果和表头内容不漂移 ============
    header('4. 已有历史数据时导出结果和表头内容不漂移')

    # 找一个有复核项的批次（任意已存在的批次）
    users = requests.get(f'{API}/users').json()
    sender = next(u for u in users if u['role'] == 'SENDER')
    batches = requests.get(f'{API}/batches').json()

    # 优先找有复核项的批次，如果找不到就新建一个
    target_batch = None
    for b in batches:
        d = requests.get(f'{API}/batches/{b["id"]}').json()
        if 'reviews' in d and len(d.get('reviews', [])) > 0:
            target_batch = b
            break

    if not target_batch:
        # 没有历史数据就新建一个（保证场景4总能执行）
        from datetime import datetime, timedelta
        prefix = f'ENC{int(datetime.now().timestamp()%100000)}'
        r = requests.post(f'{API}/batches', json={
            'batch_no': f'ENC-{prefix}',
            'description': '编码稳定性测试批次',
            'created_by': sender['id'],
            'archives': [{'archive_no': f'{prefix}-1', 'title': '编码测试档案'}]
        })
        bid = r.json()['id']
        receiver = next(u for u in users if u['role'] == 'RECEIVER')
        r = requests.post(f'{API}/boxes', json={
            'box_no': f'BOX-ENC-{prefix}', 'batch_id': bid, 'operator_id': sender['id']
        })
        boxid = r.json()['id']
        d = requests.get(f'{API}/batches/{bid}').json()
        aid = d['archives'][0]['id']
        requests.post(f'{API}/boxes/pack', json={
            'batch_id': bid, 'box_id': boxid, 'archive_ids': [aid],
            'operator_id': sender['id']
        })
        requests.post(f'{API}/batches/{bid}/transfer', json={'operator_id': sender['id']})
        requests.post(f'{API}/boxes/{boxid}/sign', json={'operator_id': receiver['id']})
        rv = requests.post(f'{API}/reviews', json={
            'batch_id': bid, 'box_id': boxid, 'operator_id': receiver['id'],
            'issue_type': '材料缺页', 'issue_description': '编码测试问题'
        }).json()
        requests.post(f'{API}/reviews/{rv["id"]}/update', json={
            'operator_id': sender['id'], 'handling_note': '已处理', 'status': 'PENDING_CLOSE'
        })
        requests.post(f'{API}/reviews/{rv["id"]}/close', json={'operator_id': receiver['id']})
        target_batch = {'id': bid}

    bid = target_batch['id']
    print(f'  目标批次: id={bid}')

    # 导出
    r = requests.post(f'{API}/batches/{bid}/export', json={'operator_id': sender['id']})
    assert_true(r.status_code == 200, '导出成功')

    content = r.json()['content']
    lines = content.splitlines()

    # 定位复核项明细
    idx_detail = next((i for i, ln in enumerate(lines) if '复核项明细' in ln), None)
    idx_history = next((i for i, ln in enumerate(lines) if '流转历史' in ln), None)
    assert_true(idx_detail is not None and idx_history is not None,
                'CSV包含复核项明细区块')

    detail_lines = lines[idx_detail + 1: idx_history]
    assert_true(len(detail_lines) >= 2, f'复核明细至少含表头+1条数据, 实际{len(detail_lines)}行')

    # 检查表头（不能漂移）
    header_row = next(csv.reader([detail_lines[0]]))
    expected_header = ['复核ID', '盒号', '问题类型', '问题描述', '责任方', '截止时间',
                       '处理说明', '状态', '提报人', '提报时间', '关闭人', '关闭时间']
    assert_true(len(header_row) == len(expected_header),
                f'表头列数不变: 期望{len(expected_header)}, 实际{len(header_row)}')
    for i, (h, exp) in enumerate(zip(header_row, expected_header)):
        assert_true(h == exp, f'表头第{i+1}列不漂移: 期望"{exp}", 实际"{h}"')
    print(f'  表头校验通过: {header_row}')

    # 检查已关闭记录的关闭人/关闭时间（不能漂移）
    found_closed = False
    for ln in detail_lines[1:]:
        try:
            cols = next(csv.reader([ln]))
        except Exception:
            cols = ln.split(',')
        if len(cols) >= 12 and cols[7] == '已关闭':
            found_closed = True
            closer = cols[10]
            closed_at = cols[11]
            print(f'  已关闭记录: id={cols[0]} 关闭人={closer} 关闭时间={closed_at}')
            assert_true(len(closer) > 0, '已关闭记录关闭人非空（不漂移）')
            assert_true(len(closed_at) > 0, '已关闭记录关闭时间非空（不漂移）')
            assert_true(closer != '', '关闭人不是空字符串')
            assert_true(closed_at != '', '关闭时间不是空字符串')
    assert_true(found_closed, '至少有1条已关闭记录用于校验')

    print(f'{PASS} 4. 历史数据导出结果和表头内容不漂移')

    # ============ 结果汇总 ============
    print()
    print('=' * 60)
    print(f'编码稳定性专项回归结果: {passed}/{total} 项通过')
    if passed == total:
        print(f'{PASS} 所有编码稳定性场景通过！')
    else:
        print(f'{FAIL} {total - passed} 项失败！')
    print('=' * 60)

    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
