const taskController = require('./controller/taskController.js')

module.exports = function router(req, res) {
  const { method, url } = req;

  if (method === "GET" && url === "/tasks") {
    return taskController.listTasks(req, res);
  }

  if (method === "POST" && url === "/tasks") {
    return taskController.createTask(req, res);
  }

  if (method === "PUT" && url.startsWith("/tasks/")) {
    const id = url.split("/")[2];
    return taskController.updateTask(req, res, id);
  }

  if (method === "DELETE" && url.startsWith("/tasks/")) {
    const id = url.split("/")[2];
    return taskController.deleteTask(req, res, id);
  }

  res.writeHead(404);
  res.end("Route not found");
};