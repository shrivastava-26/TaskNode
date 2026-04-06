const taskService = require('../service/taskService.js')


exports.listTasks = (req, res) => {
  const tasks = taskService.getAllTasks();
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(tasks));
};

exports.createTask = (req, res) => {
  let body = "";

  req.on("data", chunk => (body += chunk));
  req.on("end", () => {
    const { title } = JSON.parse(body);
    const task = taskService.addTask(title);

    res.writeHead(201, { "Content-Type": "application/json" });
    res.end(JSON.stringify(task));
  });
};

exports.updateTask = (req, res, id) => {
  let body = "";

  req.on("data", chunk => (body += chunk));
  req.on("end", () => {
    const data = JSON.parse(body);
    const updated = taskService.updateTask(id, data);

    if (!updated) {
      res.writeHead(404);
      return res.end("Task not found");
    }

    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(updated));
  });
};

exports.deleteTask = (req, res, id) => {
  taskService.deleteTask(id);
  res.writeHead(200);
  res.end("Task deleted");
};