// bof.cpp will count the times each "word" has appeared in the txyc file
// Since when you generate the txyc file, you can store upto topK cluster
// centers (word) for each feature point. So, when we count the times for
// each word, we have two different ways to compute: soft and hard, where
// soft means we will take the weighted average on all the topK word, and
// hard means we only count the first word that each feature point belongs
// to.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <assert.h>
#include <float.h>

int main(int argc, char* argv[])
{
	if(argc < 5)
	{
		fprintf(stderr, "%s txyc_file centernum topK bof_file [soft=0]\n", argv[0]);
		exit(-1);
	}

	FILE *fpTxyc, *fpBof;
	int centernum, topK;
	int soft;
	float* hist;
	int* idx;
	float* dist;
	float sumK;

	int i, j, l = 0, n;
	char tmp[65536];
	char *ptr1, *ptr2;

	// Parse the input parameters
	fpTxyc = fopen(argv[1], "r");
	assert(fpTxyc);
	centernum = atoi(argv[2]);
	topK = atoi(argv[3]);
	fpBof = fopen(argv[4], "w");
	assert(fpBof);
	if (argc == 6)
		soft = atoi(argv[5]);
	else
		soft = 0;

	// Initialization
	n = 0;
	hist = new float[centernum];
	for(j=0; j<centernum; j++)
		hist[j] = 0.0;
	idx = new int[topK];
	dist = new float[topK];
	sumK = 0.0;
	for (j=0; j<topK; j++)
	{
		idx[j] = 0;
		dist[j] = 0.0;
		sumK += (float) (1.0/(j+1));
	}

	/* The format of txyc file: "x y idx1 idx2 ... idxK"
	 * 25.027933 190.045715 1845 2910 1743 ... 
	 */
	//printf("processing %s\n", argv[1]);
	while(fgets(tmp, 65535, fpTxyc) != NULL)
	{
		l++;
		n++;
		ptr1 = tmp;

		//skip the first two numbers
		for(i = 0; i < 2; i++){
			//fprintf(stderr, "%f\n", atof(ptr1));
			ptr2 = strchr(ptr1, ' ');
			if( ptr2 == NULL ){
				fprintf(stderr, "Format error in line %d!!\n", l);
				return -1;
			}
			ptr1 = ptr2+1;
		}
		
		// Read the topK center idx for each feature point
		for(i=0; i<topK; i++)
		{
			if (ptr1 == NULL)
				break;
			// Get current center idx and the distance between the center and the feature point
			idx[i] = atoi(ptr1);
			// Get to next
			ptr2 = strchr(ptr1, ' ');
			if(ptr2 == NULL && i+1 != topK)
			{
				fprintf(stderr, "Format error in line %d:%d!!\n", l, i);
				return -1;
			}
			ptr1 = ptr2+1;
		}

		if(soft == 0)
			hist[idx[0]]++; //hard weight
		else
		{
			//soft weight by rank
			for(j=0; j<topK; j++)
				hist[idx[j]] += (float) (1.0/(j+1));
		}
	}

	if(n == 0)
		fprintf(fpBof, "\n");
	else
	{
		if(soft == 0)
		{
			// hard weight, only count the number of the top 1
			for(j=0; j<centernum; j++)
			{
				if(hist[j] != 0)
					fprintf(fpBof, " %d:%d", j+1, (int)hist[j]);
			}
		}
		else
		{
			// soft weight
			for(j=0; j<centernum; j++)
			{
				hist[j] = (float)(hist[j]/n);
				if(hist[j] != 0)
					fprintf(fpBof, " %d:%g", j+1, hist[j]/sumK);
			}
		}
		fprintf(fpBof, "\n");
	}
	
	delete [] hist;
	delete [] idx;
	delete [] dist;
	fclose(fpTxyc);
	fclose(fpBof);

	return 0;
}
