// convert.cpp has the functions which change from binary file to text file and vice versa
// and also it includes the function which can gzip and ungzip files
//
#include "convert.h"

// Convert text file to binary file
void txt2bin(const char* txtFile, const char* binFile)
{
	FILE* fpIn = fopen(txtFile, "r");
	if (fpIn == NULL)
	{
		fprintf(stderr, "Open text file %s for reading failed!\n", txtFile);
		exit(-1);
	}

	FILE* fpOut = fopen(binFile, "wb");
	if (fpOut == NULL)
	{
		fprintf(stderr, "Open binary file %s for writting failed!\n", binFile);
		exit(-1);
	}

	char temp[65536];
	int N = 0;
	while (fgets(temp, 65535, fpIn) != NULL)
	{
		N++;
	}
	fwrite(&N, sizeof(N), 1, fpOut);
	
	rewind(fpIn);
	char *ptr1, *ptr2;
	double x, y, w, u, v;
	int t;
	unsigned char data[d];
	while (fgets(temp, 65535, fpIn) != NULL)
	{
		ptr1 = temp;
		x = atof(ptr1);
		fwrite(&x, sizeof(x), 1, fpOut);

		ptr2 = strchr(ptr1, ' ');
		ptr1 = ptr2+1;
		y = atof(ptr1);
		fwrite(&y, sizeof(y), 1, fpOut);

		ptr2 = strchr(ptr1, ' ');
		ptr1 = ptr2+1;
		t = atoi(ptr1);
		fwrite(&t, sizeof(t), 1, fpOut);

		ptr2 = strchr(ptr1, ' ');
		ptr1 = ptr2+1;
		w = atof(ptr1);
		fwrite(&w, sizeof(w), 1, fpOut);

		ptr2 = strchr(ptr1, ' ');
		ptr1 = ptr2+1;
		u = atof(ptr1);
		fwrite(&u, sizeof(u), 1, fpOut);

		ptr2 = strchr(ptr1, ' ');
		ptr1 = ptr2+1;
		v = atof(ptr1);
		fwrite(&v, sizeof(v), 1, fpOut);

		ptr2 = strchr(ptr1, ' ');
		ptr1 = ptr2+1;
		for (int i=0; i<d; i++)
		{
			data[i] = (unsigned char)atoi(ptr1);
			ptr2 = strchr(ptr1, ' ');
			if (ptr2 == NULL && i+1 != d)
			{
				fprintf(stderr, "Format error in line %d in %s!!\n", i, txtFile);
				exit(-1);
			}
			ptr1 = ptr2+1;
		}
		fwrite(data, sizeof(data[0]), sizeof(data)/sizeof(data[0]), fpOut);
	}

	fclose(fpIn);
	fclose(fpOut);
}

// Convert binary file to text file
void bin2txt(const char* binFile, const char* txtFile)
{
	FILE* fpIn = fopen(binFile, "rb");
	if (fpIn == NULL)
	{
		fprintf(stderr, "Open binary file %s for reading failed!\n", binFile);
		exit(-1);
	}
	int N = 0;
	fread(&N, sizeof(N), 1, fpIn);

	FILE* fpOut = fopen(txtFile, "w");
	if (fpOut == NULL)
	{
		fprintf(stderr, "Open text file %s for writting failed!\n", txtFile);
		exit(-1);
	}

	double x, y, w, u, v;
	int t;
	unsigned char data[d];
	for (int n=0; n<N; n++)
	{
		fread(&x, sizeof(x), 1, fpIn);
		fread(&y, sizeof(y), 1, fpIn);
		fread(&t, sizeof(t), 1, fpIn);
		fread(&w, sizeof(w), 1, fpIn);
		fread(&u, sizeof(u), 1, fpIn);
		fread(&v, sizeof(v), 1, fpIn);
		fprintf(fpOut, "%f %f %d %f %f %f", x, y, t, w, u, v);

		fread(data, sizeof(data[0]), sizeof(data)/sizeof(data[0]), fpIn);
		for (int i=0; i<d; i++)
			fprintf(fpOut, " %d", (int)data[i]);

		fprintf(fpOut, "\n");
	}
	fclose(fpIn);
	fclose(fpOut);
}

void txt2gzip(const char* txtFile, const char* gzipFile)
{
	char f[1000];
	sprintf(f, "gzip -c %s > %s", txtFile, gzipFile);
	int x = system(f);
	if (x != EXIT_SUCCESS)
	{
		printf("gzip %s failed!\n", txtFile);
		exit(-1);
	}
}

void gzip2txt(const char *gzipFile, const char* txtFile)
{
	char f[1000];
	sprintf(f, "gunzip -c %s > %s", gzipFile, txtFile);
	int x = system(f);
	if (x != EXIT_SUCCESS)
	{
		printf("gunzip %s failed!\n", txtFile);
		exit(-1);
	}
}
