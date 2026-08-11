from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [""])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if code:
            print("\n==============================")
            print("AUTH_CODE:")
            print(code)
            print("==============================\n")

            self.wfile.write(
                "<h1>AUTH CODE 발급 성공</h1>"
                "<p>VS Code 터미널을 확인하세요.</p>".encode("utf-8")
            )
        else:
            self.wfile.write(
                "<h1>code가 없습니다.</h1>".encode("utf-8")
            )

server = HTTPServer(("localhost", 3000), Handler)

print("localhost:3000 서버 실행 중...")
print("이 창을 닫지 말고 Chrome에서 카카오 로그인 URL을 실행하세요.")

server.handle_request()