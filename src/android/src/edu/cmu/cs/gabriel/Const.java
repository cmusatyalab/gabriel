package edu.cmu.cs.gabriel;

import java.io.File;

import android.os.Environment;

public class Const {
	/* 
	 * Experiement variable
	 */
	
	// Transfer from the file list
	// If TEST_IMAGE_DIR is not none, transmit from the image
	public static File ROOT_DIR = new File(Environment.getExternalStorageDirectory() + File.separator + "Gabriel" + File.separator);
	public static File TEST_IMAGE_DIR = new File (ROOT_DIR.getAbsolutePath() + File.separator + "images" + File.separator);	
	
	// control VM
	public static String GABRIEL_IP = "128.2.210.197";	// hail.elijah.cs.cmu.edu
//	public static String GABRIEL_IP = "128.2.213.102";	// Cloudlet
//	public static String GABRIEL_IP = "54.202.26.12";	// Amazon West
	
	// Token
	public static int MAX_TOKEN_SIZE = 1;
	
	// Specify Allowed Application for experiement 2)
	public static String OFFLOADING_NAMES = null; 
	
	
	// image size and frame rate
	public static int MIN_FPS = 50;
	public static int IMAGE_WIDTH = 320;

	// Result File
	public static String LATENCY_FILE_NAME = "latency-" + GABRIEL_IP + "-" + MAX_TOKEN_SIZE + ".txt";
	public static File LATENCY_DIR = new File(ROOT_DIR.getAbsolutePath() + File.separator + "exp");
	public static File LATENCY_FILE = new File (LATENCY_DIR.getAbsolutePath() + File.separator + LATENCY_FILE_NAME);
}
