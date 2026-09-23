#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <gabriel/gabriel.h>

static PyObject *py_version(PyObject *self, PyObject *args) {
  (void)self;
  (void)args;
  return PyUnicode_FromString(gabriel_version());
}

static PyObject *py_add(PyObject *self, PyObject *args) {
  (void)self;
  int a, b;
  if (!PyArg_ParseTuple(args, "ii", &a, &b)) {
    return NULL;
  }
  return PyLong_FromLong(gabriel_add(a, b));
}

static PyMethodDef methods[] = {
    {"version", py_version, METH_NOARGS, "Return the gabriel library version."},
    {"add", py_add, METH_VARARGS, "Placeholder binding, replace with real functionality."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_gabriel",
    "Low-level CPython extension wrapping the gabriel C library.",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__gabriel(void) { return PyModule_Create(&moduledef); }
