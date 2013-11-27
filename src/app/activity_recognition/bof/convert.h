#ifndef CONVERT_H
#define CONVERT_H

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#define d 256

void txt2bin(const char* txtFile, const char* binFile);
void bin2txt(const char* binFile, const char* txtFile);
void txt2gzip(const char* txtFile, const char* gzipFile);
void gzip2txt(const char* gzipFile, const char* txtFile);

#endif
