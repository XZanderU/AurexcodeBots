
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from common import CONFIG, LOGS, connect, export_table, read_lines

BOT_NAME = "AurexcodeProspect/1.0"
TIMEOUT = 15
KEYWORDS = [
    "software", "desarrollo", "automatización", "automatizacion",
    "api", "integración", "integracion", "sistema de gestión",
    "transformación digital", "transformacion digital",
    "react", "aplicaciones", "procesos", "logística", "logistica",
    "mantenimiento", "servicios tecnológicos"
]

logging.basicConfig(
    filename=LOGS / "prospect.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


def normalize_domain(url):
    domain = urlparse(url).netloc.lower().split("@")[-1]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.split(":")[0]


def allowed_by_robots(url, session):
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        response = session.get(robots_url, timeout=TIMEOUT)
        if response.status_code == 404:
            return True
        if response.status_code != 200:
            logging.warning("No se pudo verificar robots.txt: %s", robots_url)
            return False

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        return parser.can_fetch(BOT_NAME, url)

    except requests.RequestException as exc:
        logging.warning("Error consultando robots.txt %s: %s", url, exc)
        return False


def scan_site(url, session):
    if urlparse(url).scheme not in ("http", "https"):
        logging.warning("URL descartada por esquema: %s", url)
        return None

    if not allowed_by_robots(url, session):
        logging.info("No se rastrea por robots.txt: %s", url)
        return None

    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = meta.get("content", "").strip() if meta else ""

    if not description:
        description = soup.get_text(" ", strip=True)[:500]

    text = f"{title} {description}".lower()
    matches = [word for word in KEYWORDS if word in text]

    if not matches:
        return None

    final_url = response.url
    domain = normalize_domain(final_url)
    if not domain:
        return None

    return {
        "domain": domain,
        "company_name": title[:200],
        "url": final_url,
        "description": description[:1000],
        "matched_keywords": ", ".join(matches),
        "source_url": url,
    }


def main():
    urls = read_lines(CONFIG / "prospect_sources.txt")
    if not urls:
        logging.warning("No hay URLs en prospect_sources.txt")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": BOT_NAME + " (contact: reemplazar-por-tu-correo)"
    })

    conn = connect()
    try:
        for url in urls:
            try:
                prospect = scan_site(url, session)
                if prospect:
                    conn.execute("""
                        INSERT INTO prospects (
                            domain, company_name, url, description,
                            matched_keywords, source_url
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(domain) DO UPDATE SET
                            company_name = excluded.company_name,
                            url = excluded.url,
                            description = excluded.description,
                            matched_keywords = excluded.matched_keywords,
                            source_url = excluded.source_url,
                            last_seen = CURRENT_TIMESTAMP
                    """, (
                        prospect["domain"],
                        prospect["company_name"],
                        prospect["url"],
                        prospect["description"],
                        prospect["matched_keywords"],
                        prospect["source_url"]
                    ))
                    conn.commit()
                    logging.info(
                        "Prospecto registrado o actualizado: %s",
                        prospect["domain"]
                    )

            except (requests.RequestException, ValueError) as exc:
                logging.warning("Error procesando %s: %s", url, exc)

    finally:
        conn.close()
        session.close()

    output = export_table("prospects", "prospects.csv")
    logging.info("Exportación finalizada: %s", output)


if __name__ == "__main__":
    main()
