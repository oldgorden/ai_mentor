import sys
sys.path.insert(0, '/home/lk/ai_mentor')
try:
    from lib.vlm_review import load_paper
    print('SUCCESS: lib.vlm_review imported successfully')
except Exception as e:
    print(f'FAIL: {e}')