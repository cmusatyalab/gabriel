#include <stdio.h>

#include <lightning/lightning.h>

int main(void) {
  printf("lightning version: %s\n", lightning_version());
  return 0;
}
