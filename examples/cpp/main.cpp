#include <iostream>

#include <gabriel/gabriel.h>

int main() {
  std::cout << "gabriel version: " << gabriel_version() << "\n";
  std::cout << "2 + 3 = " << gabriel_add(2, 3) << "\n";
  return 0;
}
