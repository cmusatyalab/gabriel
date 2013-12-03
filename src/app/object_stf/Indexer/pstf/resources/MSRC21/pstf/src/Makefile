PSTF_SOURCES=pstf/*.py pstf/innerloop_cy.c

PYTHON=python
PYLINT=pylint
CP=cp

all: python_stf.tar.gz

lint:
	-$(PYLINT) pstf

python_stf.tar.gz: setup.py $(PSTF_SOURCES)
	$(PYLINT) -E pstf
	$(PYTHON) setup.py sdist
	cp dist/python-stf-*.tar.gz python-stf.tar.gz

pstf/innerloop_cy.c: pstf/innerloop_cy.pyx
	cython pstf/innerloop_cy.pyx

clean:
	$(PYTHON) setup.py clean --all
	$(RM) *.pyc python-stf.tar.gz pstf/*.pyc pstf/*.c
	$(RM) -r dist python_stf.egg-info

.PHONY: all clean
