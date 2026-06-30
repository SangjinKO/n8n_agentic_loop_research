import os
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from google import genai
from tavily import TavilyClient

load_dotenv()

app = Flask(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
tavily = TavilyClient(api_key=TAVILY_API_KEY)

GEMINI_MODELS = [
    "gemini-3.1-flash-lite",   # 무료 쿼터 15
    "gemini-2.5-flash-lite", # 무료 쿼터 10
    "gemini-3.5-flash", # 무료 쿼터 5
]


def call_gemini(prompt: str) -> str:
    last_error = None
    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            last_error = e
    raise RuntimeError(f"All Gemini models failed: {last_error}")


@app.route("/plan", methods=["POST"])
def plan():
    data = request.get_json()
    topic = data.get("topic", "")

    print("\n" + "="*50)
    print(f"ReAct 리서치 봇")
    print(f"\n토픽: {topic}")
    print("="*50)
    print(f"\n[plan] 토픽 분석 중: {topic}", flush=True)

    prompt = f"""다음 주제에 대해 웹 검색에 사용할 가장 핵심적인 키워드 1개를 생성해주세요.
주제: {topic}

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
{{"keyword": "핵심 검색 키워드"}}"""

    text = call_gemini(prompt)
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)
    print(f"[plan] 첫 키워드: {result['keyword']}", flush=True)
    return jsonify({"keyword": result["keyword"], "topic": topic})


@app.route("/search", methods=["POST"])
def search():
    data = request.get_json()
    keyword = data.get("keyword", "") or data.get("next_keyword", "")
    previous_results = data.get("previous_results", [])
    iteration = len(previous_results) // 5

    print(f"\n[search] 검색: '{keyword}'  (iteration {iteration})", flush=True)

    response = tavily.search(query=keyword, max_results=5)
    new_results = [
        {
            "title": r.get("title", ""),
            "content": r.get("content", ""),
            "url": r.get("url", ""),
        }
        for r in response.get("results", [])
    ]
    print(f"[search] {len(new_results)}개 결과 수집", flush=True)
    return jsonify({"results": previous_results + new_results})


@app.route("/judge", methods=["POST"])
def judge():
    data = request.get_json()
    topic = data.get("topic", "")
    results = data.get("results", [])
    iteration = data.get("iteration", 1)

    total_content = "".join(r.get("content", "") for r in results)
    print(f"\n[judge] 결과 평가 중... (iteration {iteration}, 수집 {len(results)}개, 총 {len(total_content)}자)", flush=True)

    # if len(results) < 3 or len(total_content) < 500:
    if len(results) < 10 or len(total_content) < 2000: # 테스트용: 결과 10개 이상, 2000자 이상이어야 충분
        next_kw = f"{topic} 상세 정보"
        print(f"[judge] enough=False | 결과 부족", flush=True)
        print(f"[judge] 다음 키워드: {next_kw}", flush=True)
        return jsonify({"enough": False, "next_keyword": next_kw, "topic": topic, "results": results})

    results_text = "\n".join(
        f"- [{r.get('title', '')}]: {r.get('content', '')[:300]}"
        for r in results
    )

    prompt = f"""다음은 "{topic}" 주제에 대해 수집한 검색 결과입니다 (반복 횟수: {iteration}).

{results_text}

이 정보가 "{topic}"에 대한 포괄적인 요약을 작성하기에 충분한지 판단해주세요.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
- 충분하면: {{"enough": true, "next_keyword": "", "reason": "판단 이유"}}
- 부족하면: {{"enough": false, "next_keyword": "추가로 검색할 키워드", "reason": "판단 이유"}}"""

    text = call_gemini(prompt)
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)

    enough = result.get("enough", False)
    reason = result.get("reason", "")
    next_kw = result.get("next_keyword", "")
    print(f"[judge] enough={enough} | {reason}", flush=True)
    if next_kw:
        print(f"[judge] 다음 키워드: {next_kw}", flush=True)
    print(f"[route] → {'summarize' if enough else 'search'}  (enough={enough}, iteration={iteration})", flush=True)

    result["topic"] = topic
    result["results"] = results
    return jsonify(result)


@app.route("/summarize", methods=["POST"])
def summarize():
    data = request.get_json()
    topic = data.get("topic", "")
    all_results = data.get("all_results", [])

    print(f"\n[summarize] {len(all_results)}개 결과로 보고서 작성 중...", flush=True)

    results_text = "\n\n".join(
        f"### {r.get('title', '')}\n출처: {r.get('url', '')}\n{r.get('content', '')}"
        for r in all_results
    )

    prompt = f"""다음 검색 결과들을 바탕으로 "{topic}"에 대한 종합 요약을 마크다운 형식으로 작성해주세요.

{results_text}

요약은 다음을 포함해야 합니다:
- 핵심 내용
- 주요 특징 또는 발견사항
- 결론

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
{{"summary": "마크다운 형식의 요약 내용"}}"""

    text = call_gemini(prompt)
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)
    summary_len = len(result.get("summary", ""))
    print(f"[summarize] 완료 ({summary_len}자)", flush=True)
    print("\n" + "="*50)
    print("완료!")
    print("="*50 + "\n", flush=True)
    return jsonify(result)


@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(port=3000, debug=False)
