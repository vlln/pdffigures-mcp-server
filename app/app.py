import os
import logging
import httpx
import uuid
import asyncio
from pathlib import Path
from typing import Annotated, Union, Dict, Any, List, Optional

from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Form, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from fastmcp import FastMCP
from utils import save_uploaded_file, read_output_file
from service import process_pdf_extraction

# ==============================================================================
# Configuration & Initialization
# ==============================================================================

# Adherence to Knuth's literate programming style: configure first.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Use pathlib for robust path manipulation.
APP_ROOT = Path("/app")
UPLOAD_FOLDER = APP_ROOT / "uploads"
OUTPUT_FOLDER = Path(os.getenv("OUTPUT_FOLDER", str(APP_ROOT / "outputs")))

# Create directories on startup. This is idempotent.
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Centralize configuration variables.
RESOURCE_BASE_URL = os.getenv("RESOURCE_BASE_URL")
PARALLEL_DOWNLOAD_CHUNKS = int(os.getenv("PARALLEL_DOWNLOAD_CHUNKS", "4"))
DOWNLOAD_TIMEOUT = 60.0

# ==============================================================================
# Network & File Helpers
#
# Principle: Encapsulate complex operations into well-defined functions.
# This section adds the parallel download logic.
# ==============================================================================

async def _download_chunk(client: httpx.AsyncClient, url: str, start: int, end: int) -> bytes:
    """Coroutine to download a single byte range of a file."""
    headers = {'Range': f'bytes={start}-{end}'}
    response = await client.get(
        url, 
        headers=headers, 
        timeout=DOWNLOAD_TIMEOUT,
        follow_redirects=True
    )
    response.raise_for_status()
    return response.content

async def download_file_in_parallel(
    client: httpx.AsyncClient,
    url: str,
    destination_path: Path,
    num_chunks: int
) -> None:
    """
    Downloads a file in parallel chunks if the server supports it,
    otherwise falls back to a standard single-stream download.

    Args:
        client: An httpx.AsyncClient instance.
        url: The URL of the file to download.
        destination_path: The local path to save the file.
        num_chunks: The desired number of parallel download chunks.
    
    Raises:
        IOError: If the download fails for any reason.
    """
    try:
        # 1. Pre-flight HEAD request to check for Range support and get file size.
        head_response = await client.head(
            url, 
            follow_redirects=True, 
            timeout=DOWNLOAD_TIMEOUT
        )
        head_response.raise_for_status()
        final_url = str(head_response.url)
        total_size_str = head_response.headers.get('content-length')
        accept_ranges = head_response.headers.get('accept-ranges')

        if accept_ranges == 'bytes' and total_size_str and int(total_size_str) > 0 and num_chunks > 1:
            total_size = int(total_size_str)
            chunk_size = total_size // num_chunks
            logger.info(f"Server supports range requests. Starting parallel download with {num_chunks} chunks.")

            tasks = []
            for i in range(num_chunks):
                start = i * chunk_size
                end = start + chunk_size - 1 if i < num_chunks - 1 else total_size - 1
                if start >= total_size:
                    continue
                tasks.append(_download_chunk(client, final_url, start, end))
            
            # 2. Concurrently execute all download tasks.
            downloaded_chunks = await asyncio.gather(*tasks)

            # 3. Assemble the file from chunks.
            with open(destination_path, "wb") as f:
                for chunk in downloaded_chunks:
                    f.write(chunk)
            logger.info(f"Parallel download of {url} to {destination_path} complete.")

        else:
            # 4. Fallback to single-stream download.
            logger.warning(f"Server does not support range requests or file is empty. Falling back to single-stream download for {url}.")
            async with client.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                with open(destination_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
            logger.info(f"Single-stream download of {url} to {destination_path} complete.")

    except (httpx.RequestError, httpx.HTTPStatusError, asyncio.TimeoutError) as e:
        logger.error(f"Failed to download file from URL '{url}': {e}")
        raise IOError(f"Failed to download file from URL: {url}") from e

# ==============================================================================
# Core Business Logic
# ==============================================================================

async def extract_pdf_logic(
    *,
    file: Optional[UploadFile] = None,
    pdf_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Core logic for handling PDF extraction from either a file or a URL.
    This function is designed to be called directly by API endpoints or tools.
    """
    if not (file or pdf_url) or (file and pdf_url):
        raise ValueError("Either 'file' or 'pdf_url' must be provided, but not both.")

    temp_file_path: Optional[Path] = None
    original_filename: str

    try:
        if pdf_url:
            original_filename = os.path.basename(pdf_url).split('?')[0]
            
            temp_file_path = UPLOAD_FOLDER / original_filename
            
            async with httpx.AsyncClient() as client:
                await download_file_in_parallel(
                    client=client,
                    url=pdf_url,
                    destination_path=temp_file_path,
                    num_chunks=PARALLEL_DOWNLOAD_CHUNKS
                )

        elif file:
            if not file.filename:
                raise ValueError("No file selected or filename is empty.")
            original_filename = file.filename
            saved_path_str = await save_uploaded_file(file, str(UPLOAD_FOLDER))
            temp_file_path = Path(saved_path_str)

        with open(str(temp_file_path), "rb") as f:
            header = f.read(5)
            if header != b"%PDF-":
                raise IOError("File is not a valid PDF.")

        if not original_filename.lower().endswith('.pdf'):
            raise ValueError("Only PDF files are allowed.")

        if not temp_file_path or not temp_file_path.exists():
             raise ValueError("Could not determine file path to process or file is missing.")

        response_content = await run_in_threadpool(
            process_pdf_extraction,
            file_path=str(temp_file_path),
            original_filename=original_filename,
            output_dir=str(OUTPUT_FOLDER)
        )

        if "error" in response_content:
            logger.error(f"Error during PDF extraction for '{original_filename}': {response_content['error']}")
            raise RuntimeError(response_content["error"])
        
        return response_content

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"Cleaned up temporary file: {temp_file_path}")
            except OSError as e:
                logger.error(f"Error removing temporary file {temp_file_path}: {e}")


# ==============================================================================
# Helper Functions
# ==============================================================================

def construct_full_urls(data: List[Dict[str, Any]], base_url: Optional[str]) -> List[Dict[str, Any]]:
    """
    Converts relative resource paths to fully qualified URLs.
    This function is pure and has no side effects.
    """
    if not base_url:
        return data

    resources_url = base_url.rstrip('/') + '/resources/'

    for item in data:
        if 'renderURL' in item and isinstance(item['renderURL'], str):
            filename = os.path.basename(item['renderURL'])
            item['renderURL'] = resources_url + filename

    return data


# ==============================================================================
# Service Setup (MCP Tool)
# ==============================================================================

mcp = FastMCP("PDF Extraction Service")

@mcp.tool
async def extract_figures_from_pdf(pdf_url: str) -> Dict[str, Any]:
    """
    Extracts figures and tables from a PDF file provided via URL.
    This tool processes a PDF from the given URL, extracts all figures and tables,
    and returns a JSON object containing the extracted data.
    """
    try:
        extraction_result = await extract_pdf_logic(pdf_url=pdf_url)
        final_response = construct_full_urls(extraction_result, RESOURCE_BASE_URL)
        return {"result": final_response}
    except (ValueError, IOError, RuntimeError) as e:
        logger.error(f"Tool 'extract_figures_from_pdf' failed for URL '{pdf_url}': {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.exception(f"An unexpected error occurred in tool 'extract_figures_from_pdf' for URL '{pdf_url}'")
        return {"error": f"An unexpected server error occurred: {e}"}

# ==============================================================================
# API Server Setup (FastAPI)
# ==============================================================================

mcp_app = mcp.http_app(path="/")

app = FastAPI(
    title="PDF Extraction API Server",
    lifespan=mcp_app.lifespan,
    redirect_slashes=False
)

app.mount("/mcp", mcp_app)
app.mount("/resources", StaticFiles(directory=str(OUTPUT_FOLDER)), name="resources")

api_router = APIRouter(prefix="/api")

@api_router.get("/health")
async def health_check():
    """Provides a simple health check endpoint."""
    return {"status": "healthy", "service": "pdf-extraction-server"}

@api_router.post("/extract")
async def extract_figures(
    request: Request,
    file: Annotated[Optional[UploadFile], File(description="A PDF file to be processed.")] = None,
    pdf_url: Annotated[Optional[str], Form(description="The URL of a PDF file to be processed.")] = None
):
    """
    Extracts figures and tables from a given PDF file (via upload or URL).
    This endpoint serves as a thin wrapper around the core extraction logic.
    """
    try:
        extraction_result = await extract_pdf_logic(file=file, pdf_url=pdf_url)
        base_url_for_response = RESOURCE_BASE_URL or str(request.base_url)
        final_response = construct_full_urls(extraction_result, base_url_for_response)
        return JSONResponse(content=final_response, status_code=200)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IOError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception:
        logger.exception("An unexpected error occurred in the /api/extract endpoint")
        raise HTTPException(status_code=500, detail="An unexpected server error occurred.")

app.include_router(api_router)

@app.get("/")
async def root():
    """Provides basic information about the service endpoints."""
    return {
        "message": "PDF Extraction Server",
        "api_docs": "/docs",
        "api_base": "/api",
        "mcp_endpoint": "/mcp"
    }