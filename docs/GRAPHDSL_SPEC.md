# Python GraphDSL 0.1 specification

이 문서에서 `MUST`, `MUST NOT`, `SHOULD`, `MAY`는 규범적 요구사항이다.

## 1. 범위

GraphDSL은 Python AST의 시각화가 아니다. 한 node는 독립적으로 설명하고, 테스트하고,
다른 구현으로 교체할 수 있는 의미 단위다. 하나의 graph는 다음 세 interface를 지원한다.

| interface mode | benchmark | 생성 결과 |
|---|---|---|
| `function` | HumanEval, MBPP, BigCodeBench | callable Python function/module |
| `stdio` | LiveCodeBench | stdin을 읽고 stdout을 쓰는 Python program |
| `repository_patch` | SWE-bench | 기존 저장소에 적용할 unified diff |

고정 node 종류를 알고리즘이나 라이브러리별로 늘리지 않는다. BFS, dynamic programming,
CSV 정제 같은 의미는 `Compute.description` 또는 `Custom.description`에 기록한다. pandas의
각 API 역시 별도 kind가 아니라 signature가 다른 `Call` node다.

## 2. Canonical document

```json
{
  "graphdsl_version": "0.1.0",
  "id": "benchmark:task-id",
  "title": "human readable title",
  "description": "whole-task contract",
  "target": {"language": "python", "python_version": ">=3.10"},
  "interface": {"mode": "function", "entrypoint": "solve"},
  "regions": [{"id": "root", "owner": null, "role": "root"}],
  "nodes": [],
  "edges": [],
  "constraints": [],
  "examples": [],
  "metadata": {}
}
```

JSON document가 canonical representation이다. canvas 좌표는 의미가 없으므로 core document에
넣지 않는다. UI layout은 graph id와 node id를 참조하는 별도 view-state로 저장한다.

## 3. Node의 기준

한 node는 다음 조건을 만족해야 한다.

- 이름 붙일 수 있는 하나의 책임을 가진다.
- 입력과 출력 port로 contract를 설명할 수 있다.
- node만 단위 테스트하거나 예시를 붙일 수 있다.
- 구현을 바꾸어도 다른 node의 contract는 유지할 수 있다.

다음은 피한다.

- `Add`, `LoadAttr`, `Subscript` 등 AST token 수준의 과도한 분해
- 전체 문제를 감추는 단일 `SolveEverything` node
- `BFS`, `PandasReadCsv`, `RegexReplace`처럼 끝없이 증가하는 고정 kind
- 타입과 입출력이 없이 description만 있는 node

## 4. 공통 node 구조

```json
{
  "id": "normalize",
  "kind": "Compute",
  "label": "Normalize text",
  "description": "Lowercase the text and collapse consecutive whitespace.",
  "region": "root",
  "inputs": [{"id": "text", "type": "str", "required": true}],
  "outputs": [{"id": "normalized", "type": "str"}],
  "config": {},
  "constraints": ["pure", "deterministic"],
  "examples": [{"inputs": {"text": "A  B"}, "outputs": {"normalized": "a b"}}],
  "source_anchor": null
}
```

`description`은 구현 방법보다 관찰 가능한 동작을 우선 기록한다. 알고리즘 선택이 correctness
또는 complexity contract의 일부일 때만 알고리즘을 명시한다.

### 4.1 Port

Port는 node interface다. `type`은 Python type expression 또는 다음 특별 타입이다.

- `Any`: 아직 구체화되지 않은 값
- `EffectToken`: 파일, stdout, mutation 등 side-effect 실행 순서
- `ControlToken`: 값이 없는 명시적 실행 trigger가 반드시 필요한 경우
- `Error[T]`: 예외 결과
- `Artifact[T]`: file, module, symbol, repository 같은 source artifact
- `Patch[T]`: artifact에 적용할 변경

Port id는 node 안에서 유일해야 한다. input port는 기본적으로 정확히 하나의 incoming edge만
가진다. 여러 값을 받는 port는 `cardinality: "many"`를 선언해야 한다.

### 4.2 Kernel node kinds

#### 경계와 구조

- `Input`: function parameter, stdin, issue statement 등 외부 입력
- `Output`: return, stdout, patch output 등 외부 출력
- `Literal`: 상수와 configuration
- `Function`: 함수 signature와 callable boundary
- `Module`: import 및 module-level contract
- `RegionInput`, `RegionOutput`: nested region의 숨겨진 경계 pin

#### 계산

- `Compute`: 순수하거나 effect가 명시된 의미 계산
- `Custom`: 사용자가 description과 port를 정의한 확장 node
- `Call`: 함수, method, constructor, library API 호출
- `Access`: attribute, index, slice, mapping lookup
- `Construct`: list, tuple, dict, set, object 생성
- `Update`: mutation 또는 새 상태 생성

#### 제어

- `Branch`: `if/elif/else`; branch region과 결과 merge를 소유
- `Loop`: `for/while`; body region, iteration binding, loop-carried state를 소유
- `Try`: body/handler/else/finally region을 소유
- `Raise`: typed exception 발생
- `Exit`: return, break, continue 같은 구조화된 탈출

#### 효과와 검증

- `Effect`: stdout/logging/time/random 등 effect operation
- `Resource`: file, network, database, process 등 자원
- `Context`: acquire/release 또는 Python `with`
- `Assert`: runtime invariant
- `Test`: example/unit/integration test

#### 저장소 overlay

- `SourceArtifact`: 기존 repository/file/module/class/function 참조
- `Locate`: issue와 repository에서 수정 후보 symbol을 찾는 요구사항
- `Edit`: 기존 artifact의 변경 contract
- `AddArtifact`: 새 file/symbol 생성 contract
- `DeleteArtifact`: 명시된 artifact 제거 contract
- `Patch`: 여러 edit을 unified diff로 결합

## 5. Call node

라이브러리마다 kind를 만들지 않는다.

```json
{
  "id": "read_csv",
  "kind": "Call",
  "label": "pandas.read_csv",
  "description": "Read the CSV file with the requested separator.",
  "region": "root",
  "inputs": [
    {"id": "filepath_or_buffer", "type": "str", "required": true},
    {"id": "sep", "type": "str", "required": false, "default": ","},
    {"id": "effect_in", "type": "EffectToken", "required": true}
  ],
  "outputs": [
    {"id": "dataframe", "type": "pandas.DataFrame"},
    {"id": "effect_out", "type": "EffectToken"}
  ],
  "config": {
    "callable": "pandas.read_csv",
    "call_style": "function",
    "arguments": ["filepath_or_buffer"],
    "keyword_arguments": ["sep"]
  }
}
```

`call_style`은 `function`, `method`, `constructor`, `await` 중 하나다. positional/keyword
binding은 node config에 있으며 edge에는 없다.

## 6. Region과 구조화된 control flow

GraphDSL은 branch body나 loop body를 두 번째 IR로 만들지 않는다. 모든 node와 edge는 같은
document에 한 번만 존재한다. `region`은 같은 graph 안에서 lexical scope와 canvas nesting을
표현하는 metadata다.

```json
{
  "regions": [
    {"id": "root", "owner": null, "role": "root"},
    {"id": "sum_loop.body", "owner": "sum_loop", "role": "body"}
  ]
}
```

Nested region의 값은 `RegionInput`과 `RegionOutput` node를 통과한다. UI는 이 두 node를
region 경계의 pin으로 축약해 보여줄 수 있다.

### 6.1 Loop

`Loop.config`가 다음을 소유한다.

- `mode`: `for_each`, `while`, `range`, `async_for`
- `body_region`: body region id
- `iteration`: iterable/item/index binding
- `carried`: 초기값, body 입력, 다음 값, 최종 출력 mapping
- `termination`: while condition, optional maximum, break semantics

Loop를 표현하기 위해 graph back-edge를 만들지 않는다. loop-carried value는 `carried` mapping과
body의 `RegionInput`/`RegionOutput`으로 표현한다.

### 6.2 Branch

`Branch`는 condition input을 가지며 `config.branches`에 `then`, `elif`, `else` region을
기록한다. 각 region의 `RegionOutput` contract는 같아야 한다. 어떤 branch인지 edge label로
표현하지 않는다.

### 6.3 Try

`Try`는 body, handler, optional else/finally region을 소유한다. exception class와 binding은
handler config에 기록한다. exception edge를 별도로 만들지 않는다.

## 7. Edge 규칙

Edge의 전체 schema는 다음뿐이다.

```json
{
  "from": {"node": "normalize", "port": "normalized"},
  "to": {"node": "tokenize", "port": "text"}
}
```

### 7.1 MUST

1. `from.node`와 `to.node`는 존재해야 한다.
2. source port는 source node의 output이어야 한다.
3. target port는 target node의 input이어야 한다.
4. 동일한 `(from node, from port, to node, to port)` edge를 중복할 수 없다.
5. `cardinality: one` input에는 incoming edge가 최대 하나다.
6. edge object는 `from`, `to` 외의 의미 필드를 가져서는 안 된다.

### 7.2 MUST NOT

Edge에 다음 의미를 추가하면 안 된다.

- condition 또는 branch label
- 실행 순서 번호
- loop-back flag
- exception 종류
- type conversion
- argument name
- 자연어 instruction

이 의미는 node, port 또는 owning region이 소유한다.

### 7.3 Effect ordering

순수 node는 data dependency로 순서가 정해진다. side effect node는 `EffectToken`을 consume하고
produce한다. 따라서 실행 순서도 평범한 port connection으로 보인다. 독립 effect를 병렬 실행할
수 없다면 하나의 token chain으로 연결해야 한다.

### 7.4 Type compatibility

Edge 자체에는 type이 없다. validator가 양 끝 port type의 assignability를 확인한다. `Any`는
모든 타입과 임시 호환되지만, code generation 전에는 가능한 한 구체화해야 한다. 암시적 coercion이
필요하면 `Compute` 또는 `Call` node를 추가한다.

## 8. Benchmark별 profile

### 8.1 Function profile

HumanEval/MBPP는 `Function`, `Input`, `Compute`, `Branch`, `Loop`, `Call`, `Output`, `Test`를
주로 사용한다. BigCodeBench는 여기에 rich `Call`, `Resource`, `Context`, `Effect`를 추가한다.

`interface` 예시:

```json
{"mode":"function","entrypoint":"sort_even","signature":"(values: list[int]) -> list[int]"}
```

### 8.2 Stdio profile

LiveCodeBench는 I/O와 algorithm을 분리한다.

```text
stdin -> parse -> solve -> format -> stdout
```

`solve`를 하나의 opaque node로 끝내지 말고 주요 state transition 또는 독립 알고리즘 단위로
분해한다. 단, 단순 산술식을 AST node들로 쪼개지는 않는다.

### 8.3 Repository-patch profile

SWE-bench는 전체 repository를 graph로 복제하지 않는다. graph는 변경과 관련된 symbol만
`SourceArtifact`로 참조하는 overlay다.

```json
{
  "kind": "SourceArtifact",
  "source_anchor": {
    "repository": "owner/repo",
    "revision": "base commit",
    "path": "src/module.py",
    "symbol": "Parser.parse"
  }
}
```

아직 symbol을 모르면 `Locate` node가 query와 예상 artifact type을 가진다. 최종 output은
Python 문자열이 아니라 적용 가능한 unified diff다. test patch 또는 gold patch는 모델 입력에
누출하지 않는다.

## 9. LLM generation contract

### 9.1 Natural language -> GraphDSL

- 모델은 JSON object 하나만 출력한다.
- prompt에 없는 요구사항을 발명하지 않는다.
- 불확실한 구현은 `Custom` node description과 constraint로 보존한다.
- 모든 required input은 edge 또는 default를 가져야 한다.
- 원 문제의 example과 complexity requirement를 graph 최상위에도 보존한다.

### 9.2 GraphDSL -> Python

- graph에 없는 기능을 추가하지 않는다.
- node id를 생성 코드의 안정적인 trace comment 또는 source map에 남긴다.
- port type과 edge 연결을 변수 binding contract로 사용한다.
- effect token 순서를 지킨다.
- `function`/`stdio`는 실행 가능한 Python을, `repository_patch`는 unified diff만 출력한다.

### 9.3 권장 검증 순서

1. JSON parsing
2. JSON Schema validation
3. port/edge/region semantic validation
4. GraphDSL -> Python generation
5. syntax compile
6. benchmark tests
7. 실패 정보를 graph node에 대응시켜 repair

## 10. Version 0.1의 의도적 제한

- arbitrary `goto`나 unstructured control-flow는 지원하지 않는다.
- Python descriptor/metaclass/bytecode 수준 동작은 `Custom` 또는 `SourceArtifact`로 감싼다.
- type inference는 보조 기능이며 Python type checker를 대체하지 않는다.
- repository 검색 자체와 patch graph는 같은 document에 표현할 수 있지만, v0.1 evaluator는
  최종 patch correctness만 점수화한다.

## 11. Executable node ABI

기본 Python 합성은 node별로 `def <stable_symbol>(inputs, regions)`를 생성한다.
inputs는 input port ID를 key로 하는 dict이고 반환값은 output port ID를 key로 하는 dict다.
다른 node의 구현은 prompt에 제공하지 않는다. 연결된 node의 계약과 이미 전달된 값만 쓴다.
Input/Output/Literal/RegionInput/RegionOutput은 compiler가 직접 처리한다.

Loop/Branch/Try/Context의 regions는 해당 node가 소유한 region ID → callback mapping이다.
callback 입력은 `RegionInputNode.outputPort` → 값, 결과는 `RegionOutputNode.inputPort` → 값이다.
Loop는 iteration과 carried 계약에 따라 callback을 반복 호출한다. 추가 불변 입력은
config.bindings의 boundary endpoint → owner input port mapping으로 지정한다.
Branch/Try/Context는 config.region_bindings에 region별 inputs/outputs mapping을 둔다.
inputs는 boundary endpoint → owner input port, outputs는 owner output port → boundary endpoint다.
Branch.config.branches는 `{region, condition_port}`의 순서 있는 목록이며 null은 else다.

입력/결과 연결과 각 region의 DAG 실행은 compiler가 담당한다. 실제 조건·반복·예외 동작은
계약을 받은 해당 제어 node의 구현이 담당하므로 정답 여부는 실행 테스트로 확인해야 한다.
function interface는 signature가 필요하며 class method는 `Solution.method`와 self 포함 signature를
기록한다. LiveCodeBench의 class형 task는 function profile을 쓴다. repository_patch는 이 ABI의
대상이 아니며 별도의 whole-artifact 경로를 명시적으로 선택한다.
