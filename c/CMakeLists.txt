find_package(Threads REQUIRED)

add_library(gabriel
    src/common.c
    src/gabriel.c
    src/producer.c
    src/version.c
)
add_library(gabriel::gabriel ALIAS gabriel)

target_include_directories(gabriel
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
)
target_compile_definitions(gabriel PRIVATE _GNU_SOURCE)
target_compile_options(gabriel PRIVATE -Wall -Wextra)
target_link_libraries(gabriel PUBLIC Threads::Threads)

set_target_properties(gabriel PROPERTIES
    C_STANDARD 11
    C_STANDARD_REQUIRED ON
    # Needed so this static lib can also be linked into the Python
    # extension module, which is always a shared object (see the
    # top-level CMakeLists.txt's SKBUILD block).
    POSITION_INDEPENDENT_CODE ON
)

include(GNUInstallDirs)

install(TARGETS gabriel
    EXPORT gabrielTargets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

# No test suite yet - the old one (tests/test_lightning.c) tested a
# pre-rename API that no longer exists and was removed. Wire a real
# test_gabriel executable back up here, gated on GABRIEL_BUILD_TESTS
# (still declared in the top-level CMakeLists.txt), once one exists.
