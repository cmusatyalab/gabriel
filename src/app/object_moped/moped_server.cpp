/*
 * Elijah: Cloudlet Infrastructure for Mobile Computing
 * Copyright (C) 2011-2012 Carnegie Mellon University
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of version 2 of the GNU General Public License as published
 * by the Free Software Foundation.  A copy of the GNU General Public License
 * should have been distributed along with this program in the file
 * LICENSE.GPL.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
 * or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
 * for more details.
 * 
 */
#include <moped.hpp>
#include <opencv/cv.h>
#include <opencv/highgui.h>

#include <dirent.h>
#include <omp.h>

#include <unistd.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>

#define foreach( i, c ) for( typeof((c).begin()) i##_hid=(c).begin(), *i##_hid2=((typeof((c).begin())*)1); i##_hid2 && i##_hid!=(c).end(); ++i##_hid) for( typeof( *(c).begin() ) &i=*i##_hid, *i##_hid3=(typeof( *(c).begin() )*)(i##_hid2=NULL); !i##_hid3 ; ++i##_hid3, ++i##_hid2)

using namespace std;
using namespace MopedNS;


int init_client_manager();	// initialization method for client manager thread
int end_client_manager();	// tear down method for client manager

#define MAX_IMAGE_SIZE 3*1024*1024
char *image_data;

//Moped variable and Method
Moped moped;
int init_moped(string &modelsPath);
string* run_moped(string &image_name);
int close_moped();

void message() {
	cout << "usage: moped_Server [-j N]" << endl;
	cout << "    N: number of threads to use" << endl;
}

int main( int argc, char **argv ) {
	if (argc == 1) {
		// do nothing
		omp_set_num_threads(4);
		printf("Number of thread : 4\n");
	} else if (argc == 3) {
		if (strcmp(argv[1], "-j") == 0) {
			int nt = atoi(argv[2]);
			if (nt > 0) {
				printf("Number of thread : %d\n", nt);
				omp_set_num_threads(nt);
			} else {
				message();
				return -1;
			}
		}
	} else {
		message();
		return -1;
	}

	// Init MOPED module
	printf("Init MOPED Module\n");
	string modelsPath = "./moped/models";
	init_moped(modelsPath);

	// Run Socket Server
	if (init_client_manager() != 1) {
		fprintf(stderr, "Cannot run TCP Server\n");
		return -1;
	}
	while (1) {
		sleep(10000);
	}
	end_client_manager();

	return 0;
}

int init_moped(string &modelsPath){

	DIR *dp;
	struct dirent *dirp;

	if((dp  = opendir(modelsPath.c_str())) ==  NULL)
		throw string("Error opening \"") + modelsPath + "\"";

	vector<string> fileNames;
	while((dirp = readdir(dp)) != NULL) {
		string fileName =  modelsPath + "/" + string(dirp->d_name);
		if( fileName.rfind(".moped.xml") == string::npos ) continue;
		fileNames.push_back( fileName );
	}

	#pragma omp parallel for
	for(int i=0; i<(int)fileNames.size(); i++) {
		sXML XMLModel;
		XMLModel.fromFile(fileNames[i]);

		#pragma omp critical(addModel)
		moped.addModel(XMLModel);
	}
	closedir(dp);

	return 1;
}

string* run_moped(string &image_name){
	string* objects_list = new string;

	IplImage *image = cvLoadImage(image_name.c_str());
	vector<SP_Image> images;
	SP_Image mopedImage( new Image );

	mopedImage->intrinsicLinearCalibration.init( 472., 470., 312., 240.);
	mopedImage->intrinsicNonlinearCalibration.init(-2e-6, 2e-6, -2e-12, -2e-12);

	//mopedImage->intrinsicLinearCalibration.init( 811.4229, 811.5323,307.4172,248.91963 );
	//mopedImage->intrinsicNonlinearCalibration.init(-0.1750, 0.1772454, 2.2205897213455503e-04, -2.4827840641630848e-04);

	mopedImage->cameraPose.translation.init(0.,0.,0.);
	mopedImage->cameraPose.rotation.init(0.,0.,0.,1.);

	IplImage* gs = cvCreateImage(cvSize(image->width,image->height), IPL_DEPTH_8U, 1);
	cvCvtColor(image, gs, CV_BGR2GRAY);
	mopedImage->data.resize( image->width * image->height );
	for (int y = 0; y < image->height; y++)
		memcpy( &mopedImage->data[y*image->width], &gs->imageData[y*gs->widthStep], image->width );
	cvReleaseImage(&gs);

	mopedImage->width = image->width;
	mopedImage->height = image->height;
	mopedImage->name = "files";
	images.push_back(mopedImage);

	list<SP_Object> objects;
	moped.processImages(images, objects);

	foreach(object, objects){
//		clog << object->model->name << " " << object->pose << " " << object->score << endl;
		objects_list->append(object->model->name + " ");
	}

//	cvWaitKey();

	return objects_list;
}

int close_moped(){
	return -1;
}





/*
 * Server
 */
#define MAX_CLIENT_NUMBER 20
#define TCP_SERVER_PORT			9092	// TCP port number for client connection
#define MAX_CLIENT_NUMBER		20 		// Maximum concurrent number of client

static void init_client(int client_handler);
void *start_client_handler(void *arg); //socket connection thread
void *start_client_manager(void *arg); // client handler thread
int make_local_tcp_server_socket(int *port, int num_of_client);

static pthread_t client_manager_thread;			// network socket accept thread
static pthread_t client_data_handler;			// client data handler thread
typedef struct {
	int sock_fd;
	char ip_address[16];
}__attribute__((packed)) TCPClient;
static TCPClient clients[MAX_CLIENT_NUMBER];	// array structure for client fd

pthread_mutex_t client_mutex;					// client socket mutex
static fd_set clients_fdset;

/*
 * Client Thread Lock
 */
static void lock() {
//	client_lock = 1;
//	pthread_mutex_lock(&client_mutex);
}
static void unlock() {
//	pthread_mutex_unlock(&client_mutex);
//	client_lock = 0;
}
static void waiting_lock() {
//	while (client_lock) {
//		sched_yield();
//	}
//	pthread_mutex_lock(&client_mutex);
}

int init_client_manager(){
	pthread_create(&client_manager_thread, NULL, start_client_manager, NULL);
	return 1;
}

int end_client_manager(){
	pthread_cancel(client_manager_thread);
	return 1;
}

void *start_client_manager(void* args){
	struct sockaddr_in accepted_addr;
	int accepted_sock;
	int server_sock;
	int client_len;
	int i;

	// init client structure
	for (i = 0; i < MAX_CLIENT_NUMBER; i++) {
		init_client(i);
	}

	printf("Start Client handler\n");

	// start client handler
	pthread_create(&client_data_handler, NULL, start_client_handler, NULL);

	// get socket
	int port = TCP_SERVER_PORT;
	server_sock = make_local_tcp_server_socket(&port, MAX_CLIENT_NUMBER);
	if (server_sock == -1) {
		printf("Client Manager Error getting server socket!\n");
		return NULL;
	}

	printf("Client Manager start(%d).\n", port);
	while (1) {
		client_len = sizeof(accepted_addr);
		printf("Client Manager is waiting for Client.\n");
		accepted_sock = accept(server_sock, (struct sockaddr*) &accepted_addr, (socklen_t*) &client_len);

		//Get a empty slot
		for (i = 0; i < MAX_CLIENT_NUMBER; i++) {
			if (clients[i].sock_fd == 0)
				break;
		}

		// Fully Connected
		if (i == MAX_CLIENT_NUMBER) {
			close(accepted_sock);
			printf("Client Socket Full.\n");
			sleep(1 * 1000);
			continue;
		}

		printf("[%d] Client Manager Accepted new Client.\n", clients[i].sock_fd);
		strcpy(clients[i].ip_address, inet_ntoa(accepted_addr.sin_addr));
		clients[i].sock_fd = accepted_sock;
		FD_SET(accepted_sock, &clients_fdset);
	}
}

/*
 * Private method implementation
 */
static void init_client(int client_handler) {
	lock();
	memset(clients[client_handler].ip_address, 0, sizeof(clients[client_handler].ip_address));
	if (clients[client_handler].sock_fd != 0)
		FD_CLR(clients[client_handler].sock_fd, &clients_fdset);
	clients[client_handler].sock_fd = -0;
	unlock();
}


int make_local_tcp_server_socket(int *port, int num_of_client) {
	int server_sock;
	struct sockaddr_in server_addr;

	server_sock = socket(PF_INET, SOCK_STREAM, 0);
	if (server_sock == -1) {
		return -1;
	}

	server_addr.sin_family = AF_INET;
	server_addr.sin_addr.s_addr = htonl(INADDR_ANY);
	server_addr.sin_port = htons(*port);

	int option = 1;
	setsockopt(server_sock, SOL_SOCKET, SO_REUSEADDR, (void*) &option,
			sizeof(option));

	if (bind(server_sock, (struct sockaddr*) &server_addr, sizeof(server_addr))
			< 0) {
		return -1;
	}

	if (listen(server_sock, num_of_client) < 0) {
		return -1;
	}

	if (*port != 0)
		return server_sock;

	struct sockaddr_in adr_inet;
	socklen_t len_inet = (socklen_t) sizeof(adr_inet);
	getsockname(server_sock, (struct sockaddr*) &adr_inet, &len_inet);
	*port = (unsigned) ntohs(adr_inet.sin_port);

	return server_sock;
}

/*
 * Convert int from big endian to little endian
 */
int endian_swap_int(int data) {
	int ret = ((data >> 24) & 0x000000FF) | ((data << 8) & 0x00FF0000) | ((data
			>> 8) & 0x0000FF00) | ((data << 24) & 0xFF000000);
	return ret;
}


void *start_client_handler(void *arg) {
	FILE* temp_image_file;
	const char *image_filename = "./cloudlet_image.jpg";

	struct timeval timeout;
	fd_set temp_fdset;
	unsigned int msg_datasize;
	int result;
	int i;

	FD_ZERO(&clients_fdset);
	FD_ZERO(&temp_fdset);

	//allocate image data buffer
	image_data = (char *) malloc(sizeof(char) * MAX_IMAGE_SIZE);
	if(!image_data){
		printf("Error, Cannot allocate %d Memory", MAX_IMAGE_SIZE);
		assert(true);
	}

	while (1) {
		//waiting time
		timeout.tv_sec = 0;
		timeout.tv_usec = 10000;
		waiting_lock();

		temp_fdset = clients_fdset;
		result = select(FD_SETSIZE, &temp_fdset, (fd_set *) NULL,(fd_set *) NULL, &timeout);
		if (result == 0) {
			// time-out
			usleep(100); unlock(); continue;
		} else if (result == -1) {
			usleep(100); unlock();continue;
		}

		//read data from client
		for (i = 0; i < MAX_CLIENT_NUMBER; i++) {
			if (clients[i].sock_fd == 0 || !FD_ISSET(clients[i].sock_fd, &temp_fdset)){
				unlock(); continue;
			}

			printf("[%d]New Data is incoming\n", i);
			result = recv(clients[i].sock_fd, &msg_datasize, 4 * sizeof(char), MSG_WAITALL);
			if (result <= 0) {
				printf("[%d]Closed Client.\n", i); init_client(i); unlock(); continue;
			}

			printf("[%d]Data Size : %d\n", i, endian_swap_int(msg_datasize));
			msg_datasize = endian_swap_int(msg_datasize);
			if(msg_datasize > MAX_IMAGE_SIZE){
				image_data = (char*)realloc(image_data, sizeof(char) * msg_datasize);
				if(!image_data){
					printf("[%d]Cannot Allocate %d Memory.\n", i, msg_datasize);
				}
			}
			//read image data
			recv(clients[i].sock_fd, image_data, msg_datasize, MSG_WAITALL);
			printf("[%d]All data is received %d \n", i, msg_datasize);

			//save it to file --> need to be fixed into direct memory access
			temp_image_file = fopen(image_filename, "w");
			fwrite(image_data, msg_datasize, 1, temp_image_file);
			fclose(temp_image_file);

			//Run MOPED
			//string image_name =  "/home/krha/workspace/object/moped-example/test/image_0002.jpg";
			string image_name = string(image_filename);
			string *objects = run_moped(image_name);
			close_moped();
			cout << objects->c_str() << endl;
			const char *detected_objects = objects->c_str();
			printf("objects : %s\n", detected_objects);

			//return number of people in the picture
			int ret_size = endian_swap_int(strlen(detected_objects));
			send(clients[i].sock_fd, &ret_size, sizeof(int), MSG_WAITALL);
			send(clients[i].sock_fd, detected_objects, strlen(detected_objects), MSG_WAITALL);
			printf("[%d]Result is sent.\n", i);

		}
		unlock();
	}

	return NULL;
}
