cmake_minimum_required(VERSION 3.18) # 3.18+ for FindPython's Development.Module
project(gabriel C CXX)

# Turn this on to build gabriel as a .so
option(BUILD_SHARED_LIBS "Build gabriel as a shared library" OFF)
option(GABRIEL_BUILD_EXAMPLES "Build the example C/C++ programs in examples/" OFF)

add_subdirectory(c)

if(GABRIEL_BUILD_EXAMPLES)
    add_subdirectory(examples)
endif()

# SKBUILD is set when scikit-build-core drives the build, like with pip install
if(SKBUILD)
    find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
    python_add_library(_gabriel MODULE python/src/gabriel/_gabriel.c WITH_SOABI)
    target_link_libraries(_gabriel PRIVATE gabriel::gabriel)
    install(TARGETS _gabriel DESTINATION gabriel)
endif()
