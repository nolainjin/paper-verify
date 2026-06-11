# paper-verify — 한국어 가이드

**문서 속 인용을 사실검증합니다.** 마크다운/텍스트 문서에서 출처(URL · DOI ·
PMC · PMID · arXiv)를 전부 추출하고, 각 원문을 가져와 **인용된 주장이 실제로
지지되는지** LLM 심판으로 판정한 뒤, 투명한 **100점 루브릭**으로 채점해
보고서를 만듭니다. AI가 지어낸 인용·오인용·죽은 링크를 발행 전에 잡는
도구입니다. (대학원생·연구자·강사·블로거용)

전문 검토를 대체하지 않습니다 — 사람이 직접 봐야 할 출처를 **작게 추려주는
triage 도구**입니다.

## 빠른 시작 (터미널)

설치 없이 바로 ([uv](https://docs.astral.sh/uv/) 필요):

```bash
uvx paper-verify 문서.md --level L2
```

설치해서 쓰기:

```bash
pip install paper-verify        # 또는 pipx install paper-verify
# 개발용
git clone https://github.com/nolainjin/paper-verify && cd paper-verify && pip install -e ".[dev]"
```

API 키 없이도 동작합니다(`keyword` 심판 — 저신뢰 스모크 테스트). 실전 검증은
LLM 심판을 붙이세요:

```bash
export ANTHROPIC_API_KEY=sk-...
paper-verify 문서.md --level L2 --judge anthropic:claude-sonnet-4-6
```

> PyPI 배포: [pypi.org/project/paper-verify](https://pypi.org/project/paper-verify/) (v0.1.0, 2026-06-11)

## 💬 터미널 없이 — 웹챗에서

1. **아무 웹챗** (Claude / ChatGPT / Gemini, 웹검색 가능 모델):
   [`docs/webchat/webchat-prompt.ko.md`](docs/webchat/webchat-prompt.ko.md)를
   통째로 복사해 붙여넣고, 검증할 문서를 이어서 붙여넣으면 끝. 모델이 출처를
   직접 열어보고 같은 100점 루브릭으로 채점합니다.
2. **claude.ai 스킬 업로드**: 릴리즈의
   `paper-verify-webchat-skill.zip`을 claude.ai 스킬로 업로드하면 추출·채점은
   **코드가 결정적으로** 수행하고(fetch·판정은 Claude 웹도구), 점수는 CLI와
   동일한 루브릭(`--from-evidence`)에서 나옵니다.

## 검증 레벨

| 레벨 | 깊이 | 비용 |
|---|---|---|
| **L1** | 링크 생존만 (HTTP 2xx) | 네트워크만, LLM 0 |
| **L2** | 초록/제목 vs 주장 대조 (**기본**) | 인용당 LLM ~1회 |
| **L3** | 본문·수치 정합까지 | 인용당 LLM 여러 회 |

## 심판 (`--judge`, 반복 지정 = 교차검증)

| 스펙 | 요구사항 |
|---|---|
| `keyword` (기본) | 없음 — 항상 동작, 단 어휘 겹침만 보는 저신뢰 |
| `anthropic[:모델]` | extras `[anthropic]` + `ANTHROPIC_API_KEY` |
| `openai[:모델]` | extras `[openai]` + `OPENAI_API_KEY` |
| `gemini[:모델]` | extras `[gemini]` + `GEMINI_API_KEY` |
| `cli:claude` `cli:codex` `cli:gemini` | 해당 CLI가 PATH에 설치 |

심판 2개 이상이 갈리면 `--tiebreak <스펙>`이 갈린 인용만 재판정합니다.

## 100점 루브릭

| Item | Points | Criterion |
|---|---|---|
| URL accessible | 20 | HTTP 2xx |
| Author / year match | 20 / 10 / 0 | 저자+연도 모두 일치 20 / 하나만 10 / 불일치 0 / 비교할 메타데이터 없음 = 중립 10 |
| Claim match | 50 | Match = 50 · Partial = 25 · Uncertain = 15 · Mismatch = 0 · Inaccessible = 10 |
| Cross-check agreement | 10 | 심판 2+ 합의 시에만 |

| Tier | Score | 의미 |
|---|---|---|
| 🟢 A | 90–100 | 논문/공식 보고서 인용 가능 |
| 🟡 B | 70–89 | 강의/블로그 OK, 소수정 |
| 🟠 C | 50–69 | 재확인 필요 |
| 🔴 F | 0–49 | 인용 금지 — 출처 교체 |

F 인용이 하나라도 있으면 문서 전체에 ⚠️ 경고가 붙습니다.

## 에이전트에서 쓰기

- `--json`: 전체 결과를 JSON으로 stdout에 (사람용 요약은 stderr).
- `--extract-only`: 추출만 (네트워크·LLM 0).
- `--from-evidence evidence.json`: 외부에서 fetch·판정한 증거를 표준 루브릭으로
  채점 — 웹챗 스킬의 엔진. 예시: [`examples/evidence-sample.json`](examples/evidence-sample.json).
- MCP 서버: extras `[mcp]` 설치 후 `claude mcp add paper-verify -- paper-verify-mcp`.

## 한계

- LLM 심판도 초록을 오독할 수 있습니다 — 중요 인용은 교차검증(`--judge` 2개 +
  `--tiebreak`) 후 직접 확인하세요.
- 페이월 본문이 필요한 주장은 메타데이터만으로 C 티어에 머물 수 있습니다(정상).
- soft-404 감지는 휴리스틱이고, JS 위주 페이지는 본문 추출이 빈약할 수 있습니다.

MIT © 2026 진두찬 — 상세는 영문 [README.md](README.md).
