from playwright.sync_api import sync_playwright
import json
import os
import re
import requests



urls = [
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-fluminense/lOszO#id:13472822",
    "https://www.sofascore.com/pt/football/match/fortaleza-vasco-da-gama/zOsvP#id:13472816",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-vitoria/mOszO#id:13472784",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-palmeiras/nOszO#id:13472767",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-cruzeiro/eOszO#id:13472745",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-bahia/fOszO#id:14543946",
    "https://www.sofascore.com/pt/football/match/flamengo-vasco-da-gama/zOsGuc#id:13472724",
    "https://www.sofascore.com/pt/football/match/ceara-vasco-da-gama/zOsbP#id:13472707",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-botafogo/iOszO#id:14415856",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-sport-recife/jOszO#id:13472703",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-botafogo/iOszO#id:14415850",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-corinthians/hOszO#id:13472670",
    "https://www.sofascore.com/pt/football/match/juventude-vasco-da-gama/zOsFO#id:14333880",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-santos/tOszO#id:13472900",
    "https://www.sofascore.com/pt/football/match/atletico-mineiro-vasco-da-gama/zOsCO#id:13472887",
    "https://www.sofascore.com/pt/football/match/csa-vasco-da-gama/zOskP#id:13966039",
    "https://www.sofascore.com/pt/football/match/mirassol-vasco-da-gama/zOsHOi#id:13472879",
    "https://www.sofascore.com/pt/football/match/csa-vasco-da-gama/zOskP#id:13966031",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-internacional/qOszO#id:13472871",
    "https://www.sofascore.com/pt/football/match/independiente-del-valle-vasco-da-gama/zOsyUp#id:13955382",
    "https://www.sofascore.com/pt/football/match/gremio-vasco-da-gama/zOsBtc#id:13472839",
    "https://www.sofascore.com/pt/football/match/juventude-vasco-da-gama/zOsFO#id:13472833",
    "https://www.sofascore.com/pt/football/match/independiente-del-valle-vasco-da-gama/zOsyUp#id:13955162",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-botafogo/iOszO#id:13472762",
    "https://www.sofascore.com/pt/football/match/sao-paulo-vasco-da-gama/zOsGO#id:13472729",
    "https://www.sofascore.com/pt/football/match/red-bull-bragantino-vasco-da-gama/zOsZO#id:13472680",
    "https://www.sofascore.com/pt/football/match/melgar-vasco-da-gama/zOsiW#id:13640469",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-fluminense/lOszO#id:13473428",
    "https://www.sofascore.com/pt/football/match/operario-pr-vasco-da-gama/zOsJRp#id:13742605",
    "https://www.sofascore.com/pt/football/match/fortaleza-vasco-da-gama/zOsvP#id:13473419",
    "https://www.sofascore.com/pt/football/match/lanus-vasco-da-gama/zOstob#id:13640455",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-vitoria/mOszO#id:13473415",
    "https://www.sofascore.com/pt/football/match/academia-puerto-cabello-vasco-da-gama/zOsAoRb#id:13640443",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-palmeiras/nOszO#id:13473399",
    "https://www.sofascore.com/pt/football/match/operario-pr-vasco-da-gama/zOsJRp#id:13742586",
    "https://www.sofascore.com/pt/football/match/vasco-da-gama-cruzeiro/eOszO#id:13473393",
    "https://www.sofascore.com/pt/football/match/lanus-vasco-da-gama/zOstob#id:13640421"
]


output_file = "playwright_resultado.json"
resultados = []

def extrair_id(url):
    m = re.search(r'/match/.*?/(\w+)#id:(\d+)', url)
    if m:
        return m.group(2)
    return None

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()

    for i, url in enumerate(urls, start=1):
        page = context.new_page()
        captured = {}
        flags = {"completo": False}
        partida_id = extrair_id(url)

        print(f"\n[{i}/{len(urls)}] Acessando {url} ...")

        def handle_response(response):
            try:
                wanted_endings = [
                    f"/event/{partida_id}",
                    f"/event/{partida_id}/lineups"
                ]
                if any(response.url.endswith(end) for end in wanted_endings):
                    data = response.json()
                    print(f"✅ Capturado: {response.url}")

                    if response.url.endswith("/lineups"):
                        captured["lineups"] = {"url": response.url, "data": data}
                    elif response.url.endswith(f"/event/{partida_id}"):
                        captured["event"] = {"url": response.url, "data": data}

                    if "lineups" in captured and "event" in captured:
                        flags["completo"] = True
            except Exception:
                pass

        page.on("response", handle_response)
        page.goto(url, wait_until="domcontentloaded", timeout=90000)

        # espera no máximo 15s ou até capturar os dois
        for _ in range(30):
            if flags["completo"]:
                break
            if not page.is_closed():
                page.wait_for_timeout(500)

        # agora fecha a aba com segurança
        if not page.is_closed():
            page.close()

        resultados.append({
            "url_pagina": url,
            "id_partida": partida_id,
            "capturados": captured
        })

        if not captured:
            print("❌ Nenhum dado capturado.")
        else:
            print("✅ Página finalizada.")

    browser.close()

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(resultados, f, indent=4, ensure_ascii=False)

print(f"\n💾 Dados salvos em: {os.path.abspath(output_file)}")