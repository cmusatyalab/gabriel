add_library(gabriel
    src/gabriel.c
    src/lightning.c
)
add_library(gabriel::gabriel ALIAS gabriel)

target_include_directories(gabriel
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
)

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
