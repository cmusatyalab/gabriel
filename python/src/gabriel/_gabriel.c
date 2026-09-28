#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <gabriel/gabriel.h>

static PyObject *py_version(PyObject *self, PyObject *args) {
  (void)self;
  (void)args;
  return PyUnicode_FromString(gabriel_version());
}

static PyMethodDef methods[] = {
    {"version", py_version, METH_NOARGS,
     "Return the version of the underlying Gabriel C library."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_gabriel",
    "Low-level CPython extension wrapping the Gabriel C library.",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__gabriel(void) { return PyModule_Create(&moduledef); }
