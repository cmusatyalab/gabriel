/**
* Copyright 2011 Carnegie Mellon University
*
* This material is being created with funding and support by the Department of Defense under Contract No. FA8721-05-C-0003 
* with Carnegie Mellon University for the operation of the Software Engineering Institute, a federally funded research and 
* development center.  As such, it is considered an externally sponsored project  under Carnegie Mellon University's 
* Intellectual Property Policy.
*
* This material may not be released outside of Carnegie Mellon University without first contacting permission@sei.cmu.edu.
*
* This material makes use of the following Third-Party Software and Libraries which are used pursuant to the referenced 
* Licenses.  Any modification of Third-Party Software or Libraries must be in compliance with the applicable license 
* (and only if permitted):
* 
*    Android
*    Source: http://source.android.com/source/index.html
*    License: http://source.android.com/source/licenses.html
* 
*    CherryPy
*    Source: http://cherrypy.org/
*    License: https://bitbucket.org/cherrypy/cherrypy/src/697c7af588b8/cherrypy/LICENSE.txt
*
* Unless otherwise stated in any Third-Party License or as otherwise required by applicable law or agreed to in writing, 
* All Third-Party Software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express 
* or implied.
*/

package edu.cmu.cs.cloudlet.android.application.face.ui;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;

import android.util.Log;

/**
 * Utility class for dealing with files. 
 * @author ssimanta
 *
 */
public class FileUtils {
	
	public static final String LOG_TAG = FileUtils.class.getName();
	
	public static String parseDataFileToString(final String fileName) {
		try {
			final File file = new File(fileName);
			InputStream stream = new FileInputStream(file);

			int size = stream.available();
			byte[] bytes = new byte[size];
			stream.read(bytes);
			stream.close();

			return new String(bytes);

		} catch (IOException e) {
			Log.e(LOG_TAG, "IOException in reading data file  " + fileName + " \n" + e.getMessage());
		}
		return null;
	}
	
	public static boolean writeStringtoDataFile(final String contents,
			final String fileName) {
		FileWriter fileWriter = null;
		boolean success = false; 
		try {
			fileWriter = new FileWriter(fileName);

			if (fileWriter != null) {
				fileWriter.write(contents);
				fileWriter.flush();
				fileWriter.close();
				success = true; 
			}

			
		} catch (FileNotFoundException e1) {
			Log.e(LOG_TAG, "File not found " + fileName + " \n" + e1.getMessage());
			e1.printStackTrace();


		} catch (IOException ioe) {
			Log.e(LOG_TAG, "IOException while writing to file -> " + fileName + " \n" + ioe.getMessage());
			ioe.printStackTrace();
		}
		
		return success; 
	}

}
