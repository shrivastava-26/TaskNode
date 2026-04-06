const fs = require("fs");
const path = require("path");

const FILE_PATH = path.join(__dirname, "..", process.env.TASK_FILE);
console.log(FILE_PATH)

function readTasks() {
  const data = fs.readFileSync(FILE_PATH, "utf-8");
  return JSON.parse(data || "[]");
}

function writeTasks(tasks) {
  fs.writeFileSync(FILE_PATH, JSON.stringify(tasks, null, 2));
}

module.exports = {
  getAllTasks() {
    return readTasks();
  },

  addTask(title) {
    const tasks = readTasks();

    const newTask = {
      id: Date.now(),
      title,
      completed: false,
      createdAt: new Date(),
    };

    tasks.push(newTask);
    writeTasks(tasks);
    return newTask;
  },

  updateTask(id, data) {
    const tasks = readTasks();
    const index = tasks.findIndex(t => t.id == id);

    if (index === -1) return null;

    tasks[index] = { ...tasks[index], ...data };
    writeTasks(tasks);
    return tasks[index];
  },

  deleteTask(id) {
    const tasks = readTasks();
    const filtered = tasks.filter(t => t.id != id);
    writeTasks(filtered);
  }
};