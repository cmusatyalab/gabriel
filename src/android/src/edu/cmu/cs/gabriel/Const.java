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
	
	// Token
	public static int MAX_TOKEN_SIZE = 100000;
	private static final String LATENCY_FILE_NAME = "latency_token_" + MAX_TOKEN_SIZE + ".txt";
	
	// image size and frame rate
	public static int MIN_FPS = 50;
	public static int IMAGE_WIDTH = 320;	

	// Const
	
	public static File LATENCY_FILE = new File (ROOT_DIR.getAbsolutePath() + File.separator + "exp" + File.separator + LATENCY_FILE_NAME);
}
