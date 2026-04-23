import os
import sys
import argparse
import logging
import subprocess
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Optional imports for scraping, so Git init can run even without them
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    requests = None
    BeautifulSoup = None

def init_git_repo(base_dir: Path):
    """Initializes Git repository if it doesn't exist, creates boilerplate files and first commit."""
    if (base_dir / '.git').exists():
        return

    logging.info("Initializing new Git repository...")
    
    # Create boilerplate files
    gitignore_path = base_dir / '.gitignore'
    if not gitignore_path.exists():
        gitignore_path.write_text("data/\n__pycache__/\n.env\nvenv/\n*.pyc\n", encoding='utf-8')
        logging.info("Created .gitignore")

    readme_path = base_dir / 'README.md'
    if not readme_path.exists():
        readme_path.write_text(
            "# GetCourse Scraper\n\n"
            "Clean and scalable scraper for GetCourse (niifittech.ru).\n\n"
            "## Setup\n"
            "1. `python3 -m venv venv`\n"
            "2. `source venv/bin/activate`\n"
            "3. `pip install -r requirements.txt`\n",
            encoding='utf-8'
        )
        logging.info("Created README.md")

    requirements_path = base_dir / 'requirements.txt'
    if not requirements_path.exists():
        requirements_path.write_text("requests\nbeautifulsoup4\n", encoding='utf-8')
        logging.info("Created requirements.txt")

    # Create empty data folder just in case (ignored by git, but good for structure)
    data_dir = base_dir / 'data'
    data_dir.mkdir(exist_ok=True)

    # Git commands
    try:
        subprocess.run(["git", "init"], cwd=base_dir, check=True, capture_output=True)
        subprocess.run(["git", "add", ".gitignore", "README.md", "requirements.txt", Path(__file__).name], cwd=base_dir, check=True, capture_output=True)
        
        # Check if there are changes to commit
        status = subprocess.run(["git", "status", "--porcelain"], cwd=base_dir, capture_output=True, text=True)
        if status.stdout.strip():
            subprocess.run(["git", "commit", "-m", "Initial commit: Scraper engine structure"], cwd=base_dir, check=True, capture_output=True)
            logging.info("Committed initial repository structure.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Git command failed: {e.stderr.decode('utf-8') if e.stderr else str(e)}")
    except FileNotFoundError:
        logging.error("Git is not installed or not found in PATH.")

def clean_filename(name: str) -> str:
    """Cleans a string to be used as a safe filename."""
    safe = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
    return safe.replace(' ', '_')

def process_block(block, idx: int, data_dir: Path):
    """Processes a single lesson block, extracting text and video hashes."""
    # Attempt to find a title
    title_elem = block.find(['h1', 'h2', 'h3', 'h4', 'strong', 'b'])
    if title_elem and title_elem.get_text(strip=True):
        title = title_elem.get_text(strip=True)
    else:
        title = f"Lesson_{idx}"
        
    lesson_dir = data_dir / clean_filename(title)
    lesson_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract text content
    # Remove script and style tags to avoid clutter in Markdown
    for tag in block(["script", "style"]):
        tag.decompose()
        
    text_content = block.get_text(separator='\n\n', strip=True)
    if text_content:
        md_file = lesson_dir / "content.md"
        md_file.write_text(f"# {title}\n\n{text_content}", encoding='utf-8')
        logging.info(f"Saved text content -> {md_file}")

    # Extract video iframes or hashes
    videos = block.find_all(lambda tag: tag.name == 'iframe' or tag.has_attr('data-video-hash'))
    video_items = []
    
    for v in videos:
        if v.name == 'iframe' and v.get('src'):
            video_items.append(v['src'])
        elif v.has_attr('data-video-hash'):
            video_items.append(v['data-video-hash'])

    if video_items:
        links_file = lesson_dir / "video_links.txt"
        links_file.write_text("\n".join(video_items), encoding='utf-8')
        logging.info(f"Saved {len(video_items)} video links -> {links_file}")
        
        # Generate yt-dlp commands
        ytdlp_log = lesson_dir / "yt-dlp_commands.log"
        cmds = []
        for item in video_items:
            # If it's a direct hash, wrap it in a mock player link, otherwise use URL directly
            target = item if "://" in item else f"https://player.getcourse.ru/video/hash/{item}"
            cmd = f"yt-dlp --cookies cookies.txt -o \"%(title)s.%(ext)s\" \"{target}\""
            cmds.append(cmd)
            
        ytdlp_log.write_text("\n".join(cmds), encoding='utf-8')
        logging.info(f"Saved yt-dlp commands -> {ytdlp_log}")

def scrape(url_or_path: str):
    """Main scraping orchestrator."""
    if requests is None or BeautifulSoup is None:
        logging.error("Missing dependencies. Please run: pip install -r requirements.txt")
        sys.exit(1)

    base_dir = Path(__file__).parent.resolve()
    data_dir = base_dir / "data"
    data_dir.mkdir(exist_ok=True)
    
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        logging.info(f"Fetching URL: {url_or_path}")
        try:
            # Add headers to avoid basic anti-bot blockers
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(url_or_path, headers=headers, timeout=15)
            response.raise_for_status()
            html_content = response.text
        except requests.RequestException as e:
            logging.error(f"Failed to fetch URL: {e}")
            return
    else:
        path = Path(url_or_path)
        if not path.exists():
            logging.error(f"File not found: {path}")
            return
        logging.info(f"Reading local file: {path}")
        html_content = path.read_text(encoding='utf-8')

    soup = BeautifulSoup(html_content, 'html.parser')
    blocks = soup.select('.lite-block-live-wrapper')
    
    if not blocks:
        logging.warning("No elements with class '.lite-block-live-wrapper' found.")
        return

    logging.info(f"Found {len(blocks)} lesson blocks.")
    for idx, block in enumerate(blocks, start=1):
        process_block(block, idx, data_dir)

def main():
    parser = argparse.ArgumentParser(description="GetCourse clean and scalable scraper engine.")
    parser.add_argument('--url', type=str, help="URL or path to local HTML file to parse")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.resolve()
    
    # 1. Initialize git repo if not exists
    init_git_repo(base_dir)

    # 2. Run scraper if URL is provided
    if args.url:
        scrape(args.url)
    else:
        logging.info("No URL/Path provided. Git structure initialized. Use --url <link> to parse content.")

if __name__ == "__main__":
    main()
