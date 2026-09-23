#include <stdio.h>

#include <gabriel/gabriel.h>

int main(void) {
  printf("gabriel version: %s\n", gabriel_version());
  printf("2 + 3 = %d\n", gabriel_add(2, 3));
  return 0;
}
