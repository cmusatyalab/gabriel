add_executable(gabriel_example_c c/main.c)
target_link_libraries(gabriel_example_c PRIVATE lightning::lightning)

add_executable(gabriel_example_cpp cpp/main.cpp)
target_link_libraries(gabriel_example_cpp PRIVATE lightning::lightning)
