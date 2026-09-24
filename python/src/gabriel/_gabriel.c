#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <lightning/lightning.h>

static PyObject *py_lightning_version(PyObject *self, PyObject *args) {
  (void)self;
  (void)args;
  return PyUnicode_FromString(lightning_version());
}

static PyMethodDef methods[] = {
    {"lightning_version", py_lightning_version, METH_NOARGS,
     "Return the version of the underlying Lightning C library."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_gabriel",
    "Low-level CPython extension wrapping the Lightning C library.",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__gabriel(void) { return PyModule_Create(&moduledef); }
