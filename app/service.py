# Service layer for running pdffigures2 and parsing output
import subprocess
import os
import logging
import time
from utils import read_output_file

def process_pdf_extraction(file_path: str, original_filename: str, output_dir: str):
    """
    Processes a PDF file to extract figures and tables using pdffigures2.

    Args:
        file_path (str): The absolute path to the PDF file.
        original_filename (str): The original name of the PDF file.
        output_dir (str): The directory where output files will be stored.
        # resource_base_url (str): REMOVED - Service should not know about URLs.

    Returns:
        dict: A dictionary containing the extracted figures and tables metadata,
              with paths relative to the OUTPUT_FOLDER, or an error message if the extraction fails.
    """
    java_opts = os.getenv('JAVA_OPTS', '-XX:MaxRAMPercentage=75.0')
    start_time = time.time()
    logging.debug(f"Running pdffigures2 on {file_path}")

# figure-extractor-batch
# Usage: figure-extractor-batch [options] <input>
#   <input>                  input PDF(s) or directory containing PDFs
#   -i, --dpi <value>        DPI to save the figures in (default 150)
#   -s, --save-stats <value>
#                            Save the errors and timing information to the given file in JSON fromat
#   -t, --threads <value>    Number of threads to use, 0 means using Scala's default
#   -e, --ignore-error       Don't stop on errors, errors will be logged and also saved in `save-stats` if set
#   -q, --quiet              Switches logging to INFO level
#   -d, --figure-data-prefix <value>
#                            Save JSON figure data to '<data-prefix><input_filename>.json'
#   -c, --save-regionless-captions
#                            Include captions for which no figure regions were found in the JSON data
#   -g, --full-text-prefix <value>
#                            Save the document and figures into '<full-text-prefix><input_filename>.json
#   -m, --figure-prefix <value>
#                            Save figures as <figure-prefix><input_filename>-<Table|Figure><Name>-<id>.png. `id` will be 1 unless multiple figures are found with the same `Name` in `input_filename`
#   -f, --figure-format <value>
#                            Format to save figures (default png)
    jar_path = os.getenv('PDFFIGURES_JAR_PATH', '/pdffigures2/pdffigures2.jar')
    work_dir = os.getenv('PDFFIGURES_WORK_DIR', '/pdffigures2')

    base_command = [
        'java', java_opts, '-Dsun.java2d.cmm=sun.java2d.cmm.kcms.KcmsServiceProvider', '-jar', jar_path,
        file_path,
        "-m", output_dir,
        "-d", output_dir,
        "--dpi", "300"
    ]
    result = subprocess.run(base_command, capture_output=True, text=True, cwd=work_dir)
    if result.returncode != 0:
        logging.error(f"Command failed with exit code {result.returncode}")
        logging.error(f"STDOUT: {result.stdout}")
        logging.error(f"STDERR: {result.stderr}")
        return {"error": result.stderr}

    end_time = time.time()
    processing_time = int((end_time - start_time) * 1000)
    logging.debug(f"Processing time: {processing_time} ms")
    base_filename = os.path.splitext(os.path.basename(original_filename))[0]
    metadata_filename = f"{base_filename}.json"
    metadata_path = os.path.join(output_dir, metadata_filename)
    logging.debug(f"Metadata file: {metadata_path}")

    figures_data = read_output_file(metadata_path)
    if figures_data is None:
        return {"error": f"Output file not found: {metadata_path}"}

    return figures_data
