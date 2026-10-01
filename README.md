# GraphIR pseudocode experiments

현재 실험 경로는 `자연어 → Python형 pseudocode → compact AST GraphIR → Python`이다.
GraphIR은 모델이 작성하는 별도 DSL이 아니다. Qwen은 pseudocode와 최종 Python만
생성하고, 정적 컴파일러가 AST 문장을 Input, Resource, Assign, Update, Call, Loop,
Branch, Assert, Return, Control 노드로 압축해 data/control/state edge를 만든다.

형식은 EMNLP 2023 **Prompting with Pseudo-Code Instructions**의 §3에 기반한다.
Python형 함수 선언, docstring, 제어문, 구현되지 않은 설명적 helper 호출을 사용한다.
논문은 NLP 지시 표현을 평가했다. 이 저장소의 파싱 제약, GraphIR 변환 및 코드 생성
파이프라인은 추가 설계이며, 학회에서 이미 검증된 코드 생성 방법으로 주장하지 않는다.
[논문](https://aclanthology.org/2023.emnlp-main.939/) ·
[실험 규격과 문헌 범위](docs/PSEUDOCODE_EXPERIMENT.md)

## 실행

기존 `graphir` 환경, 정규화 데이터, Qwen endpoint와 Docker 평가기를 사용한다.
추가 Python 의존성은 없다. 최초 데이터 준비 시:

```bash
pip install -r requirements.txt
python scripts/fetch_benchmarks.py --output-dir data/normalized
```

GPU 서버에서 Qwen을 켜 둔 상태로 smoke 실험:

```bash
python scripts/run_experiments.py \
  --config experiments/pseudocode_smoke_v23.json \
  --stage all
```

HumanEval 164개만 전체 평가:

```bash
python scripts/run_experiments.py \
  --config experiments/pseudocode_humaneval_v23.json \
  --stage all
```

HumanEval 164개 + MBPP 공식 test 500개:

```bash
python scripts/run_experiments.py \
  --config experiments/pseudocode_full_v23.json \
  --stage all
```

기본 실험은 다음 네 조건을 순서대로 수행한다. 같은 문제의 pseudocode 생성은 캐시로
공유하므로 조건별로 다른 계획을 생성하지 않는다.

| 조건 | Python 생성기 입력 |
| --- | --- |
| source-pseudocode-examples-1x1 | 원본 명세 + pseudocode + 별도 공개 예제 sidecar |
| graphir-only-1x1 | GraphIR + 공개 함수 인터페이스 |
| graphir-examples-1x1 | GraphIR + 공개 함수 인터페이스 + 별도 공개 예제 sidecar |
| source-graphir-examples-1x1 | 원본 명세 + GraphIR + 별도 공개 예제 sidecar |

기본 Qwen→Python 조건은 기본 설정에 없다.
함께 비교하려면 `experiments/pseudocode_ablation_full_v23.json`을 사용한다.
이 baseline도 같은 공개 예제와 일반 Python ABI를 받는다.

구조화된 공개 예제는 GraphIR 객체나 `source_specification.public_examples`에 저장하지
않는다. 최종 Python 생성 요청의 최상위 `public_examples` sidecar로만 전달되며, 없는
경우 필드 자체를 생략한다. 원본 명세 조건에서는 원문 안의 doctest를 그대로 보존한다.
따라서 `graphir-only-1x1`과 `graphir-examples-1x1`의 GraphIR은 완전히 동일하고, 두
조건의 차이는 두 번째 단계에 구조화된 공개 예제를 제공했는지뿐이다.

각 조건은 계획 후보 1개, 전체 Python 후보 1개를 사용한다. 계획을 함수별로 나눠
여러 Python 후보를 탐색하지 않는다. 구문 실패는 실패로 기록하고 재생성하지 않는다.
`prepare → generate → export → evaluate → summarize` 순서로 실행된다.
생성 코드는 호스트에서 실행하지 않고 기존 Docker 평가기에서 테스트한다.

출력은 `outputs/qwen7b-pseudocode-<설정명>-v23/` 아래에 저장된다.

- `<조건>/results.jsonl`: pseudocode, 컴파일된 GraphIR, Python, 오류와 토큰·시간
- `<조건>/evaluation/<benchmark>/results.jsonl`: 개별 정답 판정
- `summary.csv`: pseudocode/graph 유효율, Pass@1, 신뢰구간
- `shared-pseudocode/`: 조건 간 공유하는 계획 및 추론 기록

## 핵심 파일

- `scripts/pseudocode_graphir.py`: 실행 없는 parser/compiler 및 검증
- `scripts/build_pseudocode_eval.py`: 공개 입력만으로 요청 구성
- `scripts/run_pseudocode_pipeline.py`: 계획 1회 + 코드 1회 및 resume
- `prompts/nl_to_pseudocode.md`, `prompts/pseudocode_to_python.md`: 두 단계 prompt
- `scripts/run_experiments.py`: 데이터 준비부터 평가까지 실행
- `scripts/benchmark_requests.py`, `scripts/llm_client.py`,
  `scripts/public_interface.py`: 생성 방식에 독립적인 공통 기능

`function` 및 `stdio` 입력을 지원한다. LiveCodeBench의 `Solution.method`도
보존한다. BigCodeBench/LiveCodeBench 평가는 기존 공식 adapter를 사용하며 별도의
공식 평가 이미지가 필요하다. SWE-bench patch 생성과 APPS 데이터 adapter는
이번 pseudocode 경로에서 지원하지 않는다.

검증:

```bash
python -m unittest discover -s tests -v
python scripts/run_experiments.py --config experiments/pseudocode_smoke_v23.json --stage all --dry-run
```

## 이전 실험

이전 v8–v17 등의 설정은 `experiments/legacy/`로 옮겼다. 기존 결과는 그대로 보존된다.
과거 재현 및 Parsel 비교에 필요한 legacy 모듈과 테스트는 남아 있지만 새 생성 경로는
GraphDSL JSON 계약, 지역 노드 ABI, schema-constrained JSON 생성, 예제 검색기를
불러오지 않는다. 이전 코드 실행 문서는 historical 경로에 대한 설명이다.

Parsel 원본 알고리즘과 `third_party/parsel`은 변경하지 않는다.
기존 환경과 배포 안내: [평가 환경](docs/EVALUATION.md),
[GitHub 관리](docs/PUBLIC_GITHUB_RELEASE.md).
