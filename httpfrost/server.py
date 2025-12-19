import http.server
import socketserver
import os
import io
import urllib.parse
import zipfile
import argparse
import re
import time
from urllib.parse import unquote, quote

PORT = 8000
BASE_DIR = os.path.abspath(os.getcwd())
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB max upload

class SecureFileSharingHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):

    def list_directory(self, path):
        """Generate directory listing with checkboxes, download form, and upload form."""
        try:
            entries = os.listdir(path)
        except OSError:
            self.send_error(404, "No permission to list directory")
            return None

        entries.sort(key=lambda a: a.lower())
        displaypath = unquote(self.path)

        r = []
        r.append('<!DOCTYPE html>')
        r.append('<html><head><meta charset="utf-8"><title>Directory listing for %s</title></head>' % displaypath)
        r.append('<body>')
        r.append(f'<h2>Directory listing for {displaypath}</h2>')
        r.append('<hr>')

        # Multi-select download form
        r.append('<form method="POST" action="" enctype="application/x-www-form-urlencoded">')
        r.append('<table border="1" cellpadding="5" cellspacing="0">')
        r.append('<tr><th>Select</th><th>Name</th><th>Size</th></tr>')

        # Parent directory link (no checkbox)
        if displaypath != '/':
            parent = os.path.dirname(displaypath.rstrip('/'))
            if not parent.endswith('/'):
                parent += '/'
            r.append(f'<tr><td></td><td><a href="{parent}">.. (parent directory)</a></td><td></td></tr>')

        for name in entries:
            fullname = os.path.join(path, name)
            display_name = name + ('/' if os.path.isdir(fullname) else '')
            linkname = name + ('/' if os.path.isdir(fullname) else '')

            # Size formatting
            if os.path.isdir(fullname):
                size = '--'
            else:
                try:
                    size = self.format_size(os.path.getsize(fullname))
                except OSError:
                    size = 'N/A'

            # Checkbox value is relative path from current directory
            checkbox_value = urllib.parse.quote(linkname)

            r.append(f'<tr>')
            r.append(f'<td><input type="checkbox" name="files" value="{checkbox_value}"></td>')
            r.append(f'<td><a href="{linkname}">{display_name}</a></td>')
            r.append(f'<td>{size}</td>')
            r.append(f'</tr>')

        r.append('</table>')
        r.append('<br><input type="submit" value="Download Selected">')
        r.append('</form>')

        # Upload form
        r.append('<hr>')
        r.append('<h3>Upload file</h3>')
        r.append('<form enctype="multipart/form-data" method="POST">')
        r.append('<input name="uploadfile" type="file" required>')
        r.append('<input type="submit" value="Upload">')
        r.append('</form>')

        r.append('<hr>')
        r.append('</body></html>')

        encoded = '\n'.join(r).encode('utf-8', 'surrogateescape')

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()

        return io.BytesIO(encoded)

    def do_POST(self):
        content_type = self.headers.get('Content-Type', '')
        if content_type.startswith('multipart/form-data'):
            self.handle_file_upload()
        else:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            post_params = urllib.parse.parse_qs(post_data.decode('utf-8'))
            self.handle_zip_download(post_params)

    def handle_zip_download(self, post_params):
        selected_files = post_params.get('files')
        if not selected_files:
            self.send_response(303)
            self.send_header('Location', self.path)
            self.end_headers()
            return

        safe_paths = []
        for f in selected_files:
            f_decoded = urllib.parse.unquote(f)
            full_path = os.path.abspath(os.path.join(BASE_DIR, f_decoded))
            if self.is_path_safe(full_path):
                safe_paths.append(full_path)

        if not safe_paths:
            self.send_response(303)
            self.send_header('Location', self.path)
            self.end_headers()
            return

        # If exactly one file (not dir) selected, send it directly
        if len(safe_paths) == 1 and os.path.isfile(safe_paths[0]):
            filepath = safe_paths[0]
            try:
                with open(filepath, 'rb') as f:
                    content = f.read()
                filename = os.path.basename(filepath)
                quoted_filename = quote(filename)

                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', f'attachment; filename="{quoted_filename}"')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception as e:
                self.send_error(500, f"Error reading file: {e}")
                return

        # Otherwise, zip multiple files and folders
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for path in safe_paths:
                if os.path.isdir(path):
                    for root, _, files in os.walk(path):
                        for file in files:
                            full_file = os.path.join(root, file)
                            arcname = os.path.relpath(full_file, BASE_DIR)
                            zipf.write(full_file, arcname)
                else:
                    arcname = os.path.relpath(path, BASE_DIR)
                    zipf.write(path, arcname)

        zip_buffer.seek(0)
        zip_filename = "download.zip"
        quoted_zip_filename = quote(zip_filename)

        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Disposition', f'attachment; filename="{quoted_zip_filename}"')
        self.send_header('Content-Length', str(len(zip_buffer.getbuffer())))
        self.end_headers()
        self.wfile.write(zip_buffer.read())

    def handle_file_upload(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > MAX_UPLOAD_SIZE:
            self.send_error(413, "File too large")
            return

        boundary = self.headers.get_boundary()
        if not boundary:
            self.send_error(400, "No boundary in multipart/form-data")
            return

        remainbytes = content_length
        line = self.rfile.readline()
        remainbytes -= len(line)
        if not boundary.encode() in line:
            self.send_error(400, "Content does not start with boundary")
            return

        # Read Content-Disposition header line
        line = self.rfile.readline()
        remainbytes -= len(line)
        disposition = line.decode().strip()
        filename = None
        if 'filename=' in disposition:
            filename = disposition.split('filename=')[1].strip('"')
            filename = self.sanitize_filename(filename)

        # Skip Content-Type line and empty line
        line = self.rfile.readline()
        remainbytes -= len(line)
        line = self.rfile.readline()
        remainbytes -= len(line)

        if not filename:
            self.send_error(400, "Can't find filename")
            return

        save_path = os.path.join(BASE_DIR, filename)

        try:
            with open(save_path, 'wb') as out_file:
                prev_line = self.rfile.readline()
                remainbytes -= len(prev_line)
                while remainbytes > 0:
                    line = self.rfile.readline()
                    remainbytes -= len(line)
                    if boundary.encode() in line:
                        out_file.write(prev_line.rstrip(b'\r\n'))
                        break
                    else:
                        out_file.write(prev_line)
                        prev_line = line
            print(f"[+] Uploaded file saved: {save_path}")
        except Exception as e:
            print(f"[!] Upload failed: {e}")
            self.send_error(500, f"Upload failed: {e}")
            return

        self.send_response(303)
        self.send_header('Location', self.path)
        self.end_headers()

    def sanitize_filename(self, filename):
        filename = os.path.basename(filename)
        filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
        if len(filename) > 100:
            filename = filename[:100]
        filename = f"{int(time.time())}_{filename}"
        return filename

    def is_path_safe(self, path):
        abs_path = os.path.abspath(path)
        return abs_path.startswith(BASE_DIR)

    def format_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

def main():
    parser = argparse.ArgumentParser(description='Secure File sharing HTTP Server with upload & ZIP download')
    parser.add_argument('port', nargs='?', type=int, default=8000, help='Port to listen on')
    args = parser.parse_args()

    PORT = args.port
    Handler = SecureFileSharingHTTPRequestHandler

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving HTTP on 0.0.0.0 port {PORT} (http://<your_ip>:{PORT}/) ...")
        httpd.serve_forever()

if __name__ == "__main__":
    main()
