require("dotenv").config();
const http = require("http");
const router = require('./index.js')

const PORT = process.env.PORT;

const server = http.createServer((req, res) => {

  res.setHeader("Access-Control-Allow-Origin", "http://localhost:5173");
  res.setHeader(
    "Access-Control-Allow-Methods",
    "GET, POST, PUT, DELETE, OPTIONS"
  );
  res.setHeader(
    "Access-Control-Allow-Headers",
    "Content-Type"
  );

  if (req.method === "OPTIONS") {
    res.writeHead(204);
    return res.end();
  }

  router(req, res);
});

server.listen(PORT, () => {
  console.log(`✅ Server running on port ${PORT}`);
});