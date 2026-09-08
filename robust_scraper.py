import os
import time
import json
import uuid
import requests
import arxiv
import pandas as pd
from urllib.parse import urlparse
import pymupdf4llm
from duckduckgo_search import DDGS
import ssl
import urllib3

# Global SSL Bypass for restricted environments
ssl._create_default_https_context = ssl._create_unverified_context
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Monkey-patch requests to disable verify globally for all methods and sessions
old_request = requests.Session.request
def new_request(self, method, url, **kwargs):
    kwargs['verify'] = False
    return old_request(self, method, url, **kwargs)
requests.Session.request = new_request

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(BASE_DIR, "downloads", "pdfs")
INDEX_CSV = os.path.join(BASE_DIR, "SCRAPER_MASTER_INDEX.csv")
FAILED_DOWNLOADS_FILE = os.path.join(BASE_DIR, "failed_downloads.txt")
OUTPUT_JSONL = os.path.join(BASE_DIR, "radar_massive_training.jsonl")

KEYWORDS = [
    "radar systems", "radar introduction", "radar equations", "radar mathematics", "radar estimations", 
    "radar signals", "radar trace", "radar target tracking", "radar track processing", "track state estimation", 
    "radar track management", "interacting multiple model", "imm tracking", "joint probabilistic data association", 
    "jpda", "multiple hypothesis tracking", "mht radar", "track before detect", "radar gating", 
    "extended kalman filter", "unscented kalman filter", "particle filter tracking", "alpha beta tracker", 
    "sensor fusion radar", "kinematic classification", "trajectory classification", "target motion classification", 
    "radar feature extraction", "non-cooperative target recognition", "nctr radar", "high resolution range profile", 
    "hrrp radar", "jet engine modulation", "jem radar", "micro-doppler signature", "rcs signature", 
    "radar cross section analysis", "deep learning radar classification", "cnn radar classification", 
    "nato radar doctrine", "us army air defense doctrine", "military radar doctrine", "air defense radar", 
    "airborne early warning radar", "awacs tracking", "fighter jet radar", "aesa radar tracking", 
    "phased array radar tracking", "electronic countermeasures", "ecm radar", "electronic counter-countermeasures", 
    "eccm radar", "anti-jamming radar", "stealth detection radar", "vlo target detection", 
    "low observable target tracking", "synthetic aperture radar", "inverse synthetic aperture radar", 
    "sar isar classification", "asterix cat062", "asterix cat048", "stanag 4607", "gmti radar", 
    "ground moving target indicator", "radar target detection", "cfar detection", "constant false alarm rate", 
    "probability of detection", "radar waveform design", "pulse doppler radar", "fmcw radar tracking"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

# --- HELPER FUNCTIONS ---
def setup_dirs():
    os.makedirs(PDF_DIR, exist_ok=True)
    if not os.path.exists(INDEX_CSV):
        # Create with headers if it doesn't exist
        df = pd.DataFrame(columns=["Source", "Title", "URL", "Snippet", "Matched_Keywords", "Original_PDF_MB"])
        df.to_csv(INDEX_CSV, index=False)

def clean_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "._- ")[:60].strip()

def is_already_downloaded(url: str, title: str) -> bool:
    if not os.path.exists(INDEX_CSV):
        return False
    try:
        df = pd.read_csv(INDEX_CSV)
        if df.empty:
            return False
        # Check by URL or exact Title
        if url in df['URL'].values or title in df['Title'].values:
            return True
        return False
    except:
        return False

def update_index(source, title, url, snippet, keywords, file_path):
    size_mb = 0.0
    if os.path.exists(file_path):
        size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
    
    new_row = pd.DataFrame([{
        "Source": source,
        "Title": title,
        "URL": url,
        "Snippet": snippet[:500] if snippet else "Not Recorded", # Limit snippet size
        "Matched_Keywords": keywords,
        "Original_PDF_MB": size_mb
    }])
    
    new_row.to_csv(INDEX_CSV, mode='a', header=not os.path.exists(INDEX_CSV), index=False)

def log_failed_download(source, url, reason):
    with open(FAILED_DOWNLOADS_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{source}] URL: {url} | Reason: {reason}\n")

def download_pdf(url: str, filepath: str) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, stream=True, timeout=20)
        if r.status_code == 200 and ('pdf' in r.headers.get('Content-Type', '').lower() or url.lower().endswith('.pdf')):
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            time.sleep(1)
            return True
        else:
            return False
    except Exception as e:
        return False

# --- SCRAPER MODULES ---

def scrape_arxiv(keywords, limit_per_keyword=5):
    print("\n🚀 [1/4] arXiv Scraping Started...")
    client = arxiv.Client(page_size=limit_per_keyword, delay_seconds=2, num_retries=3)
    
    for keyword in keywords:
        print(f"🔍 Arxiv Search: {keyword}")
        search = arxiv.Search(
            query=f'all:"{keyword}"',
            max_results=limit_per_keyword,
            sort_by=arxiv.SortCriterion.Relevance
        )
        
        try:
            for paper in client.results(search):
                if is_already_downloaded(paper.pdf_url, paper.title):
                    continue
                
                fname = f"ARXIV_{clean_filename(paper.title)}.pdf"
                filepath = os.path.join(PDF_DIR, fname)
                
                print(f"   📥 Downloading: {paper.title[:50]}...")
                try:
                    paper.download_pdf(dirpath=PDF_DIR, filename=fname)
                    update_index("ARXIV", paper.title, paper.pdf_url, paper.summary, keyword, filepath)
                    time.sleep(1)
                except Exception as e:
                    log_failed_download("ARXIV", paper.pdf_url, str(e))
        except Exception as e:
            print(f"   ⚠️ Arxiv Query Error: {e}")

def scrape_nasa(keywords, limit_per_keyword=5):
    print("\n🚀 [2/4] NASA NTRS API Scraping Started...")
    base_url = "https://ntrs.nasa.gov/api/citations/search"
    
    for keyword in keywords:
        print(f"🔍 NASA Search: {keyword}")
        params = {"q": keyword, "page": {"size": limit_per_keyword}}
        try:
            res = requests.get(base_url, params=params, headers=HEADERS, timeout=15)
            if res.status_code != 200:
                continue
            
            data = res.json()
            for item in data.get("results", []):
                title = item.get("title", "nasa_doc")
                abstract = item.get("abstract", "")
                downloads = item.get("downloads", [])
                
                for d in downloads:
                    pdf_link = d.get("links", {}).get("pdf")
                    if pdf_link:
                        full_url = f"https://ntrs.nasa.gov{pdf_link}"
                        
                        if is_already_downloaded(full_url, title):
                            continue
                            
                        fname = f"NASA_{clean_filename(title)}.pdf"
                        filepath = os.path.join(PDF_DIR, fname)
                        
                        print(f"   📥 NASA Downloading: {title[:50]}...")
                        if download_pdf(full_url, filepath):
                            update_index("NASA_NTRS", title, full_url, abstract, keyword, filepath)
                        else:
                            log_failed_download("NASA_NTRS", full_url, "Download failed or not a PDF")
                        break
        except Exception as e:
            print(f"   ⚠️ NASA API Error: {e}")

def scrape_semantic_scholar(keywords, limit_per_keyword=5):
    print("\n🚀 [3/4] Semantic Scholar OpenAccess Scraping Started...")
    api_url = "https://api.semanticscholar.org/graph/v1/paper/search"
    
    for keyword in keywords:
        print(f"🔍 Scholar Search: {keyword}")
        params = {
            "query": keyword,
            "limit": limit_per_keyword,
            "openAccessPdf": True,
            "fields": "title,abstract,openAccessPdf"
        }
        try:
            res = requests.get(api_url, params=params, headers=HEADERS, timeout=15)
            if res.status_code == 200:
                items = res.json().get("data", [])
                for paper in items:
                    pdf_info = paper.get("openAccessPdf")
                    if pdf_info and "url" in pdf_info:
                        pdf_url = pdf_info["url"]
                        title = paper.get("title", "scholar_doc")
                        abstract = paper.get("abstract", "")
                        
                        if is_already_downloaded(pdf_url, title):
                            continue
                            
                        fname = f"SCHOLAR_{clean_filename(title)}.pdf"
                        filepath = os.path.join(PDF_DIR, fname)
                        
                        print(f"   📥 Scholar Downloading: {title[:50]}...")
                        if download_pdf(pdf_url, filepath):
                            update_index("SEMANTIC_SCHOLAR", title, pdf_url, abstract, keyword, filepath)
                        else:
                            log_failed_download("SEMANTIC_SCHOLAR", pdf_url, "Download failed or not a PDF")
        except Exception as e:
            print(f"   ⚠️ Semantic Scholar Error: {e}")

def scrape_duckduckgo_military(keywords, limit_per_keyword=3):
    print("\n🚀 [4/4] DuckDuckGo Military & Doctrine Scraping Started...")
    
    for keyword in keywords:
        # Construct a targeted query for PDFs on military/academic domains
        dork_query = f'{keyword} filetype:pdf'
        print(f"🔍 Web Search: {dork_query}")
        
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(dork_query, max_results=limit_per_keyword))
                
                for r in results:
                    url = r.get("href")
                    title = r.get("title", "web_doc")
                    snippet = r.get("body", "")
                    
                    if not url or not url.lower().endswith('.pdf'):
                        continue
                        
                    if is_already_downloaded(url, title):
                        continue
                        
                    fname = f"MILITARY_{clean_filename(title)}.pdf"
                    filepath = os.path.join(PDF_DIR, fname)
                    
                    print(f"   📥 Web Downloading: {title[:50]}...")
                    if download_pdf(url, filepath):
                        update_index("WEB_SEARCH", title, url, snippet, keyword, filepath)
                    else:
                        log_failed_download("WEB_SEARCH", url, "Download failed or blocked by server")
                    time.sleep(2) # Be gentle with web sources
        except Exception as e:
            print(f"   ⚠️ DuckDuckGo Search Error: {e}")

# --- PARSER MODULE ---
def parse_pdfs_to_jsonl():
    print("\n🧠 PyMuPDF4LLM Parsing to JSONL Started...")
    pdf_files = [f for f in os.listdir(PDF_DIR) if f.endswith(".pdf")]
    
    if not pdf_files:
        print("❌ No PDFs found to process.")
        return
        
    # Read already processed sources if jsonl exists to avoid duplicate work
    processed_sources = set()
    if os.path.exists(OUTPUT_JSONL):
        with open(OUTPUT_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    processed_sources.add(data.get("source"))
                except:
                    pass
                    
    records_added = 0
    with open(OUTPUT_JSONL, "a", encoding="utf-8") as out_f:
        for filename in pdf_files:
            if filename in processed_sources:
                continue
                
            pdf_path = os.path.join(PDF_DIR, filename)
            print(f"   ⚙️ Parsing: {filename[:45]}...")
            try:
                # Use pymupdf4llm to convert PDF to markdown
                md_text = pymupdf4llm.to_markdown(pdf_path)
                
                # Semantic chunking (2000 chars windows)
                chunk_size = 2000
                for i in range(0, len(md_text), chunk_size):
                    chunk = md_text[i:i + chunk_size].strip()
                    if len(chunk) > 200: # Skip very short/empty chunks
                        record = {
                            "id": str(uuid.uuid4()),
                            "source": filename,
                            "text": chunk
                        }
                        out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        records_added += 1
            except Exception as e:
                print(f"   ⚠️ Error parsing {filename} -> {e}")
                
    print(f"\n✅ Processing complete. Added {records_added} chunks to {OUTPUT_JSONL}.")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    setup_dirs()
    print("="*60)
    print("      ROBUST RADAR CORPUS SCRAPER INITIATED")
    print("="*60)
    
    # We will use all KEYWORDS for the full run
    
    scrape_arxiv(KEYWORDS, limit_per_keyword=20)
    scrape_nasa(KEYWORDS, limit_per_keyword=15)
    scrape_semantic_scholar(KEYWORDS, limit_per_keyword=15)
    scrape_duckduckgo_military(KEYWORDS, limit_per_keyword=10)
    
    parse_pdfs_to_jsonl()
    
    print("\n✅ Script execution finished.")
