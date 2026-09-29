# Normalized benchmark data

`normalized/*.jsonl`은 `scripts/fetch_benchmarks.py`로 생성한다. 각 record는 다음 필드를 가진다.

- `prompt`: 모델에 제공 가능한 primary prompt와 원본 variant
- `starter_code`, `entrypoint`, `public_tests`: 공개 task context
- `reference`: corpus 분석과 공식 평가 adapter용 정답/테스트; 모델 입력 금지
- `metadata`: task id, repository/base commit, platform 등
- `provenance`: 원본 URL, dataset revision, projection 정보

MBPP 원본 974개 prompt는 모두 보존한다. 논문 평가용 `--official-eval-only`는 공식 분할인
task ID 11–510의 500개 test task만 선택한다. task 1–10은 prompting, 511–600은 validation,
601–974는 train으로 취급한다.

현재 고정 revision은 BigCodeBench `v0.1.4`, LiveCodeBench `release_v6`, SWE-bench
`test` split이다. HumanEval과 MBPP는 공식 GitHub 원본을 사용한다.

LiveCodeBench 공식 dataset은 executable loader와 수 GB의 private tests를 포함한다. 이 저장소는
공식 release_v6의 공개 Parquet conversion mirror에서 prompt/public column만 projection하며,
1,055개의 task count를 강제 검증한다. private tests는 원 benchmark evaluator가 관리해야 한다.

SWE-bench의 full repository는 JSONL에 포함하지 않는다. `repo`와 `base_commit`으로 checkout한
workspace가 prompt context의 나머지를 제공한다. Qwen 입력 생성기는 `reference.patch`,
`reference.test_patch`, FAIL_TO_PASS/PASS_TO_PASS를 포함하지 않는다.
