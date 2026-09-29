# Python GraphDSL benchmark lab

이 저장소는 자연어 문제를 하나의 편집 가능한 그래프로 바꾸고, 같은 그래프에서
Python 코드 또는 repository patch를 생성하는 실험용 규격과 데이터 파이프라인이다.

지원하는 benchmark corpus:

- HumanEval: 164 function-completion tasks
- MBPP: 974 basic Python tasks
- BigCodeBench v0.1.4: 1,140 tasks, complete/instruct prompt를 모두 보존
- LiveCodeBench release_v6: 1,055 competitive-programming tasks
- SWE-bench test: 2,294 repository issue-resolution tasks

합계는 5,627 task이며, prompt variant를 모두 펼치면 현재 corpus에서 8,486개의 Qwen
요청이 만들어진다.

GraphDSL의 원칙은 두 가지다.

1. 하나의 canonical graph만 존재한다. 텍스트 표현과 캔버스는 같은 graph를 보는 두 view다.
2. edge는 오직 `source output port -> target input port` 연결이다. 조건, 순서, 타입,
   loop-back 등의 의미를 edge에 기록하지 않는다.

## 파일

- `docs/GRAPHDSL_SPEC.md`: node, port, region, edge의 규범적 설계
- `schemas/graphdsl.schema.json`: JSON Schema Draft 2020-12
- `prompts/nl_to_graphdsl.md`: Qwen용 자연어 -> GraphDSL system prompt
- `prompts/graphdsl_to_python.md`: Qwen용 GraphDSL -> Python/patch system prompt
- `docs/PIPELINE.md`: compact prompt, constrained decoding, validation, retrieval 실행 방법
- `docs/EVALUATION.md`: 실험 matrix, sandbox와 공식 evaluator 실행 방법
- `docs/PARSEL_GRAPHDSL_1X1.md`: Parsel/GraphDSL의 Qwen 7B 단일후보 비교 프로토콜
- `docs/PUBLIC_GITHUB_RELEASE.md`: 공개 GitHub 저장소 최초 배포 및 업데이트 절차
- `experiments/matrix.example.json`: direct/full/ablation 실험 설정 예시
- `demonstrations/`: 오염 여부를 감사할 수 있는 planner/synthesizer few-shot catalog
- `scripts/fetch_benchmarks.py`: 전체 benchmark prompt를 공통 JSONL로 정규화
- `scripts/analyze_corpus.py`: 전 corpus 및 reference solution/patch 구조 분석
- `scripts/build_qwen_eval.py`: Qwen inference용 JSONL 생성
- `scripts/build_parsel_eval.py`, `scripts/run_parsel_pipeline.py`: NL→Parsel과 1×1 함수 합성
- `scripts/schema_validation.py`: GraphDSL schema subset의 dependency-free 검증
- `scripts/validate_graph.py`: JSON Schema와 graph 의미 규칙의 결정론적 검증
- `scripts/validate_artifact.py`: 생성 Python syntax 또는 unified diff 형식 검증
- `scripts/retrieve_demonstrations.py`: interface-filtered deterministic example retrieval
- `scripts/build_direct_eval.py`, `scripts/run_direct_generation.py`: NL -> Python baseline
- `scripts/export_predictions.py`: 다섯 benchmark 공식 입력 형식 exporter
- `scripts/run_safe_functional_eval.py`: HumanEval/MBPP 격리 실행
- `scripts/run_official_evaluator.py`: BigCodeBench/LiveCodeBench/SWE-bench harness adapter
- `scripts/run_experiments.py`: prepare/generate/export/evaluate matrix orchestration
- `scripts/summarize_experiment.py`: gate rate, Pass@1, Wilson interval 집계
- `sandbox/`: generated code를 위한 제한된 Docker runtime
- `examples/`: function, stdio, repository-patch 예시

## 데이터 준비

Python 3.10 이상이 필요하다. LiveCodeBench는 원본의 private test column이 수 GB이므로
prompt column만 원격 projection하기 위해 DuckDB를 사용한다.

```bash
conda env create -f environment.yml
conda activate graphir
python -m unittest discover -s tests -v

python scripts/fetch_benchmarks.py --output-dir data/normalized
python scripts/analyze_corpus.py \
  --input-dir data/normalized \
  --output docs/CORPUS_ANALYSIS.md \
  --json-output data/corpus_analysis.json
```

Conda를 사용하지 않는 경우에는 Python 3.10 이상의 독립 환경에서 다음처럼 설치한다.

```bash
python -m pip install -r requirements.txt
```

이 환경은 corpus 준비와 GraphIR pipeline 실행을 담당한다. Qwen은 OpenAI-compatible
endpoint로 호출하므로 GPU 서버의 CUDA/PyTorch/vLLM 환경은 여기에서 분리한다. Parsel도
`sandbox/Dockerfile.parsel`에 고정된 별도 컨테이너에서 실행한다.

LiveCodeBench의 효율적 column projection에는 공식 `release_v6`를 Parquet으로 변환한
공개 mirror를 사용한다. 각 레코드에는 공식 upstream과 mirror provenance를 모두 남기며,
task 수가 1,055가 아니면 fetch가 실패한다. `--skip-livecodebench`로 이를 생략할 수 있다.

SWE-bench의 `problem_statement`는 전부 저장하지만 codebase 전체를 prompt 문자열에
복제하지 않는다. 대신 `repo`와 `base_commit`을 보존한다. 실제 SWE-bench 실행 시에는
그 commit을 checkout한 workspace가 모델의 repository context가 된다.

## Qwen 입력 생성

```bash
python scripts/build_qwen_eval.py \
  --input-dir data/normalized \
  --output data/qwen/nl_to_graphdsl.jsonl \
  --num-demonstrations 1 \
  --expand-variants
```

생성 파일은 각 줄이 다음 구조다.

```json
{"custom_id":"humaneval:HumanEval/0","messages":[{"role":"system","content":"..."},{"role":"user","content":"..."}],"metadata":{"benchmark":"humaneval"}}
```

Qwen tokenizer의 `apply_chat_template` 또는 vLLM/OpenAI-compatible endpoint에 그대로
전달할 수 있다. 모델 출력은 먼저 `scripts/validate_graph.py`로 검증하고, 통과한 graph만
두 번째 `graphdsl_to_python` 단계에 넣는 것을 권장한다.

로컬 vLLM/Ollama 등 OpenAI-compatible Qwen endpoint를 두 단계로 직접 호출할 수도 있다.

```bash
python scripts/run_qwen_pipeline.py \
  --input data/qwen/nl_to_graphdsl.jsonl \
  --output data/qwen/results.jsonl \
  --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-Coder-7B-Instruct \
  --constraint-mode response_format \
  --num-code-demonstrations 1 \
  --limit 20
```

기본 pipeline은 schema-constrained decoding을 사용하고, 생성된 graph에 같은 schema를 다시
적용한 뒤 semantic validator를 통과한 경우에만 두 번째 단계를 호출한다. 구형 vLLM API는
`--constraint-mode structured_outputs`, 제약 없는 ablation은 `--constraint-mode none`을 사용한다.
서로 다른 checkpoint나 endpoint를 사용하는 방법은 `docs/PIPELINE.md`에 정리되어 있다.

## 평가 단위

두 단계를 분리해 기록한다.

- `NL -> GraphDSL`: JSON parse rate, schema-valid rate, semantic-valid rate
- `GraphDSL -> Python`: 원 benchmark의 pass@1 또는 resolved rate
- end-to-end: 원 prompt에서 최종 실행 성공까지의 비율
- editability: 한 node description 변경 시 바뀐 Python line/symbol 범위

원 benchmark evaluator를 정답 판정의 유일한 기준으로 사용한다. GraphDSL validator는
구조적 오류를 잡지만 프로그램의 의미적 정답을 보장하지 않는다.

## Parsel과 1×1 비교

GraphDSL은 현재 노드별 함수를 생성하고 edge를 결정론적으로 연결한다. Loop/Branch는
구현된 내부 region callback을 호출한다. `--synthesis-mode whole`은 종전 전체 합성 방식이다.

Parsel은 `third_party/parsel`의 고정 원본 커밋을 수정 없이 Docker 안에서 실행한다.
직접 만든 parser/assembler는 제거했다. 원본 CodeGen까지 사용하며 API 전송만 로컬 Qwen
completion endpoint에 연결한다. 원본의 함수 합성 기본값(500 tokens, temperature 0.6)을
유지하므로 1×1은 후보 수 비교이며 동일 토큰 예산 비교라는 의미는 아니다.

`experiments/parsel_graphdsl_1x1.json`은 4개 Python benchmark 비교 설정이며,
`experiments/parsel_graphdsl_smoke.json`은 HumanEval/MBPP 각 3문제 설정이다.
실행 준비와 제한은 `docs/PARSEL_GRAPHDSL_1X1.md`를 따른다. 개발 환경에는 Docker/Qwen이
없어 실제 GPU 추론·컨테이너 평가 점수는 아직 검증되지 않았다.
