find_package(Threads REQUIRED)

add_library(lightning
    src/common.c
    src/consumer.c
    src/producer.c
)
add_library(lightning::lightning ALIAS lightning)

target_include_directories(lightning
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
)
target_compile_definitions(lightning PRIVATE _GNU_SOURCE)
target_compile_options(lightning PRIVATE -Wall -Wextra)
target_link_libraries(lightning PUBLIC Threads::Threads)

set_target_properties(lightning PROPERTIES
    C_STANDARD 11
    C_STANDARD_REQUIRED ON
    # Needed so this static lib can also be linked into the Python
    # extension module, which is always a shared object (see the
    # top-level CMakeLists.txt's SKBUILD block).
    POSITION_INDEPENDENT_CODE ON
)

include(GNUInstallDirs)

install(TARGETS lightning
    EXPORT lightningTargets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

if(LIGHTNING_BUILD_TESTS)
    add_executable(test_lightning tests/test_lightning.c)
    target_link_libraries(test_lightning PRIVATE lightning::lightning)
    target_compile_definitions(test_lightning PRIVATE _GNU_SOURCE)
    target_compile_options(test_lightning PRIVATE -Wall -Wextra)
    set_target_properties(test_lightning PROPERTIES C_STANDARD 11)
    add_test(NAME lightning COMMAND test_lightning)
endif()
