# Benchmark corpus analysis

이 보고서는 `data/normalized/*.jsonl`의 모든 레코드를 읽어 생성했다. prompt keyword는
요구사항의 하한을 보여주는 휴리스틱이며 의미 분류 정답으로 해석하면 안 된다. AST 표는
reference solution이 공개된 corpus만 대상으로 task별 syntax 존재 여부를 집계한다.

## Corpus

| benchmark | tasks | interface | prompt median | p95 | max | starter code |
|---|---|---|---|---|---|---|
| humaneval | 164 | function | 396 | 926 | 1360 | 164 |
| mbpp | 974 | function | 268 | 522 | 4335 | 2 |
| bigcodebench | 1140 | function | 607 | 1201 | 3464 | 1140 |
| livecodebench | 1055 | stdio | 1364 | 2531 | 3950 | 444 |
| swebench | 2294 | repository_patch | 1115 | 5150 | 57256 | 0 |

총 task 수: **5627**. BigCodeBench의 complete/instruct와 기타 distinct prompt
variant는 각 record 안에 함께 보존되므로 task 수와 prompt variant 수는 다를 수 있다.

## Prompt requirement signals

| signal | humaneval | mbpp | bigcodebench | livecodebench | swebench |
|---|---|---|---|---|---|
| stdin/stdout | 0 | 4 | 2 | 603 | 82 |
| file/resource | 3 | 6 | 348 | 32 | 627 |
| network/url | 0 | 3 | 86 | 1 | 1113 |
| database/table | 1 | 2 | 474 | 11 | 399 |
| regex/text | 5 | 38 | 73 | 56 | 51 |
| graph/tree | 2 | 3 | 15 | 63 | 113 |
| dynamic programming | 0 | 0 | 0 | 0 | 0 |
| sorting/search | 17 | 59 | 48 | 35 | 140 |
| exception/error | 0 | 5 | 334 | 15 | 845 |
| class/object | 2 | 2 | 338 | 453 | 873 |
| async/concurrency | 0 | 1 | 32 | 51 | 129 |
| generator/iterator | 0 | 1 | 9 | 5 | 84 |
| complexity/performance | 2 | 3 | 10 | 0 | 61 |
| repository change | 0 | 4 | 52 | 0 | 1091 |

## Reference Python syntax coverage

| GraphDSL capability | humaneval | mbpp | bigcodebench |
|---|---|---|---|
| Branch | 107 | 447 | 613 |
| Loop | 87 | 410 | 382 |
| Try/Raise | 2 | 2 | 398 |
| Context/Resource | 0 | 0 | 171 |
| Call | 143 | 767 | 1140 |
| Access | 114 | 560 | 1140 |
| Construct | 68 | 200 | 845 |
| Comprehension | 47 | 194 | 349 |
| Update | 108 | 783 | 1133 |
| Function | 164 | 974 | 1140 |
| Class | 0 | 4 | 10 |
| Import | 28 | 194 | 1140 |
| Generator | 0 | 3 | 0 |
| Async | 0 | 0 | 0 |

집계 결과가 정당화하는 최소 kernel은 `Compute`, `Call`, `Access`, `Construct`,
`Update`, `Branch`, `Loop`, `Try/Raise`, `Context/Resource`, `Function`, `Test`다.
comprehension이나 generator는 별도 고정 node로 강제하지 않고 작은 경우 `Compute`,
사용자가 내부 상태를 편집해야 하는 경우 `Loop`로 승격한다.

## LiveCodeBench distribution

| platform | tasks |
|---|---|
| atcoder | 602 |
| codeforces | 9 |
| leetcode | 444 |

| difficulty | tasks |
|---|---|
| easy | 322 |
| hard | 350 |
| medium | 383 |

LiveCodeBench에는 reference solution이 포함되지 않으므로 AST 통계로 node coverage를
추정하지 않는다. 문제 형식이 stdio와 method starter code를 모두 포함할 수 있으므로
`interface.mode=stdio` 아래에서도 starter signature를 metadata로 보존해야 한다.

## SWE-bench repository overlay

| repository | tasks |
|---|---|
| astropy/astropy | 95 |
| django/django | 850 |
| matplotlib/matplotlib | 184 |
| mwaskom/seaborn | 22 |
| pallets/flask | 11 |
| psf/requests | 44 |
| pydata/xarray | 110 |
| pylint-dev/pylint | 57 |
| pytest-dev/pytest | 119 |
| scikit-learn/scikit-learn | 229 |
| sphinx-doc/sphinx | 187 |
| sympy/sympy | 386 |

Gold patch에서 관측한 변경 file 수는 중앙값 1.0, p95 4, 최대 31다. 이 값은
graph가 저장소 전체를 복제하는 대신 관련 `SourceArtifact`와 `Edit`만 참조해야 한다는
근거다. Gold patch는 분석에만 사용하며 Qwen 입력에는 절대 포함하지 않는다.

## Design decisions

1. edge schema는 `from`/`to`만 유지한다. corpus의 조건·반복·예외·API argument 의미는
   edge 종류가 아니라 owning node와 port contract로 표현할 수 있다.
2. BigCodeBench의 library 다양성은 API별 node kind가 아니라 동적 `Call` signature를
   요구한다.
3. LiveCodeBench는 `parse -> solve -> format` 분리를 요구하고, loop-carried state는
   `Loop.config.carried`와 region boundary로 표현한다.
4. SWE-bench는 `Locate -> SourceArtifact -> Edit -> Test -> Patch` overlay를 요구한다.
5. reference가 없는 task에서도 요구사항 손실을 검사하도록 graph-level constraints와
   examples를 원 prompt에서 그대로 보존한다.
