# 검증 스크립트

05·33 문서의 수치와 정리를 공식 RRSI 구현으로 직접 확인한 스크립트다.

```bash
git clone https://github.com/google-research/rrsi.git rsi-ref/ref-rrsi
git -C rsi-ref/ref-rrsi checkout be50316e1db05914068a973f322770ef08ed7ba1
python3 verify_examples.py rsi-ref     # 05 문서 §8 검산 벡터 (Table 6 사례, 예산표, Dream 장난감 세계)
python3 verify_bound.py rsi-ref        # 33 문서 §4 정확 경계 조기 종료 정리 무작위 검증
```

2026-10-01 실행 결과:
- verify_examples.py: R0-A 통과, R0-B 는 ν=0..4 모두 불허, 엔지니어링 R2 통과, 예산표·AUC(0.4556) 05 문서와 일치.
- verify_bound.py: 4,000 시도 중 조기 종료 1,788 건에서 승자·𝒯_t·𝓑_t(n_prune 2, 4, 이후 6 라운드)·N_t 가 전체 평가와 모두 같음.
