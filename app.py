import flask

METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"]

app = flask.Flask(__name__, static_folder=None)
flask.cli.show_server_banner = lambda *args, **kwargs: None


@app.route("/", methods=METHODS)
@app.route("/<path:path>", methods=METHODS)
def hello(path=""):
    return "Hello, World!\n", 200


if __name__ == "__main__":
    print("Server running at http://127.0.0.1:3000/", flush=True)
    app.run(host="0.0.0.0", port=3000, debug=False)
