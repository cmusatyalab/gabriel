package edu.cmu.cs.cloudlet.android.application.face.batch;

import java.io.BufferedWriter;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileFilter;
import java.io.FileInputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.Writer;
import java.net.Socket;
import java.net.UnknownHostException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.List;

import edu.cmu.cs.cloudlet.android.R;
import android.app.Activity;
import android.app.ProgressDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.AsyncTask;
import android.os.Bundle;
import android.preference.PreferenceManager;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.View.OnClickListener;
import android.widget.Button;
import android.widget.TextView;
import android.widget.Toast;
import edu.cmu.cs.cloudlet.android.application.face.network.FacerecIOService;
import edu.cmu.cs.cloudlet.android.application.face.network.FacerecRect;
import edu.cmu.cs.cloudlet.android.application.face.network.ImageResponseMessage;

public class FaceAndroidBatchClientActivity extends Activity implements
		OnClickListener {
	public static final String LOG_KEY = "LOG_KEY";

	private static final String FACE_LOG_DIR = ".";

	public static final int MENU_ID_SETTINGS = 92189;
	public static final int MENU_ID_CLEAR = 111163;

	private String ipAddress;
	private int portNumber;
	private String directoryString;

	private Socket socket;
	private File directory;
	private List<File> fileList;

	private DataOutputStream outToServer;
	private DataInputStream inFromServer;

	private TextView textView;
	private TextView currentDirTextView;
	private Button sendButton;

	private String log = "";

	private long requestSendTime = 0L;
	private long responseReceivedTime = 0L;
	private long rttForCurrentRequest = 0L;
	private long rttForPreviousRequest = 0L;

	@Override
	public void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);
		setContentView(R.layout.speechmain);

		textView = (TextView) findViewById(R.id.text);
		textView.setText(log);
		currentDirTextView = (TextView) findViewById(R.id.current_dir_text);

		sendButton = (Button) findViewById(R.id.send_button);
		sendButton.setOnClickListener(this);

		loadPreferneces();

		directory = new File(directoryString);
		if (!directory.exists()) {
			Toast.makeText(this, "Directory [" + directoryString + "]does not exist", Toast.LENGTH_LONG)
			.show();
		}

		fileList = Arrays.asList(directory.listFiles(new FileFilter() {
			@Override
			public boolean accept(File pathname) {
				if (pathname.toString().toLowerCase().endsWith(".jpg")
						|| pathname.toString().toLowerCase().endsWith(".jpeg")) {
					return true;
				}
				return false;
			}
		}));
	}

	@Override
	protected void onResume() {
		loadPreferneces();

		super.onResume();
	}

	@Override
	public boolean onCreateOptionsMenu(Menu menu) {
		menu.add(0, MENU_ID_SETTINGS, 0, getString(R.string.menu_settings));
		menu.add(0, MENU_ID_CLEAR, 1, getString(R.string.menu_clear));
		return super.onCreateOptionsMenu(menu);
	}

	@Override
	public boolean onMenuItemSelected(int featureId, MenuItem item) {
		switch (item.getItemId()) {
		case MENU_ID_SETTINGS:
			startActivity(new Intent(FaceAndroidBatchClientActivity.this,
					FacePreferenceActivity.class));
			break;
		case MENU_ID_CLEAR:
			log = "";
			textView.setText(log);
			break;
		default:
			break;
		}
		return super.onMenuItemSelected(featureId, item);
	}

	public void loadPreferneces() {
		SharedPreferences prefs = PreferenceManager
				.getDefaultSharedPreferences(this);
		this.ipAddress = prefs.getString(getString(R.string.pref_ipaddress),
				getString(R.string.default_ipaddress));
		this.portNumber = Integer.parseInt(prefs.getString(
				getString(R.string.pref_portnumber),
				getString(R.string.default_portnumber)));
		this.directoryString = prefs.getString(
				getString(R.string.pref_directory),
				getString(R.string.default_directory));
		currentDirTextView.setText(directoryString);
	}

	class SendFace extends AsyncTask<Void, String, String> {
		ProgressDialog progreeDialog;

		@Override
		protected void onPreExecute() {
			progreeDialog = new ProgressDialog(
					FaceAndroidBatchClientActivity.this);
			progreeDialog.setCancelable(false);

			progreeDialog.setMessage("Connecting to face server...");
			updateLog("Connecting to face server...");
			progreeDialog.show();
			super.onPreExecute();

		}

		@Override
		protected void onPostExecute(String result) {
			progreeDialog.dismiss();
			if (result == null) {
				updateLog("No response or error from face server...");
			}
			super.onPostExecute(result);
		}

		@Override
		protected void onProgressUpdate(String... values) {
			progreeDialog.setMessage(values[0]);
			updateLog(values[0]);
			super.onProgressUpdate(values);
		}

		@Override
		protected String doInBackground(Void... params) {
			Writer bufWriter = null;
			String currentOutput = null;
			try {

				// init the socket and set socket properties
				try {
					socket = new Socket(ipAddress, portNumber);
					socket.setTcpNoDelay(true);
					socket.setSendBufferSize(1024 * 100);
				} catch (UnknownHostException e) {
					System.err.println("Error: unknown host or port "
							+ ipAddress + ":" + portNumber);
					// e.printStackTrace();
				}

				publishProgress("Connected to face server "
						+ socket.getInetAddress() + " on port "
						+ socket.getPort());

				// associated the input and output streams
				if (socket != null && socket.isConnected()
						&& !socket.isInputShutdown()
						&& !socket.isOutputShutdown()) {
					System.out
							.println("Successfully opened a TCP connection to IP ->"
									+ ipAddress + ",  PORT -> " + portNumber);

					outToServer = new DataOutputStream(socket.getOutputStream());
					inFromServer = new DataInputStream(socket.getInputStream());
				}

				SimpleDateFormat df = new SimpleDateFormat("yyyyMMdd-hhmmssSSS");
//				File f = new File(CloudletEnv.instance().getFilePath(
//						CloudletEnv.FACE_LOG_DIR)
//						+ File.separator
//						+ "face_"
//						+ df.format(new Date())
//						+ ".log");
				File f = new File(FACE_LOG_DIR
						+ File.separator
						+ "face_"
						+ df.format(new Date())
						+ ".log");
				bufWriter = new BufferedWriter(new FileWriter(
						f.getAbsolutePath()), 2048);
				bufWriter.write(new Date()
						+ " : ------------------ FACE LOG START \n");
				bufWriter.write("INPUT_FILE" + "\t" + "START_TIME" + "\t"
						+ "END_TIME" + "\t" + "LATENCY" + "\t" + "JITTER"
						+ "\t" + "OUTPUT \n");
				int filesProccessed = 1;
				int fileCount = fileList.size();
				for (final File file : fileList) {
					publishProgress("Sending " + filesProccessed + " / "
							+ fileCount + " file(s) \n" + file.getName() + "\n"
							+ "File size is " + file.length() + " bytes");
					requestSendTime = System.currentTimeMillis();
					sendImageRequest(file);
					publishProgress("Finished sending " + file.getName());

					publishProgress("Getting response from server...");
					// int responseSize = inFromServer.readInt();
					ImageResponseMessage responseMsg = handleResponse();
					responseReceivedTime = System.currentTimeMillis();

					publishProgress("Response is - " + responseMsg.toString()
							+ " bytes");

					rttForCurrentRequest = responseReceivedTime
							- requestSendTime;
					
					if( responseMsg.name == null || responseMsg.name.trim().length() == 0 )
						currentOutput = "N/A";
					else
						currentOutput = responseMsg.name; 

					bufWriter.write(file.getName()
							+ "\t"
							+ requestSendTime
							+ "\t"
							+ responseReceivedTime
							+ "\t"
							+ rttForCurrentRequest
							+ "\t"
							+ Math.abs(rttForCurrentRequest
									- rttForPreviousRequest) + "\t" + currentOutput
							+ "\n");

					publishProgress("----------");
					publishProgress(currentOutput);
					publishProgress("Request Send Time: " + requestSendTime);
					publishProgress("Response Recieved Time: "
							+ responseReceivedTime);
					publishProgress("RTT Current Request: "
							+ rttForCurrentRequest);
					publishProgress("RTT Previous Request: "
							+ rttForPreviousRequest);
					publishProgress("----------");
					rttForPreviousRequest = rttForCurrentRequest;
					filesProccessed++;
				}
				bufWriter.write(new Date()
						+ " : ------------------ FACE LOG END \n");
				bufWriter.flush();

			} catch (UnknownHostException e) {
				publishProgress("An UnknownHostException has occured.");
				e.printStackTrace();
				return null;
			} catch (IOException e) {
				publishProgress("An IOException has occured.");
				e.printStackTrace();
				return null;
			} finally {
				if (bufWriter != null)
					try {
						bufWriter.close();
						
						if( inFromServer != null )
							inFromServer.close();
						if( outToServer != null )
							outToServer.close();
						if( socket != null )
							socket.close();
						
					} catch (IOException e) {
						e.printStackTrace();
					}

				
			}
			return currentOutput;
		}
	}

	public void sendImageRequest(File file) {
		try {
			int fileLength = (int) (file.length());
			FileInputStream fis = new FileInputStream(file);
			byte[] jpegImageBytes = new byte[fileLength];
			fis.read(jpegImageBytes);

			// IMP: Write all the data in Network Byte Order (Big Endian)
			// write message type. Since we are using a DataOutputStream
			// "writeInt"
			// will write "int" in Network Byte Order
			outToServer.writeInt(FacerecIOService.MESSAGE_TYPE_JPEG_IMAGE);

			// write message length
			outToServer.writeInt(jpegImageBytes.length);

			// We use a ByteBuffer make sure the image bytes are written in
			// network
			// byte order.
			ByteBuffer byteBuf = ByteBuffer.wrap(jpegImageBytes);
			byteBuf.order(ByteOrder.BIG_ENDIAN);
			outToServer.write(byteBuf.array());

		} catch (IOException io) {
			io.printStackTrace();
		}
	}

	private ImageResponseMessage handleResponse() {

		ImageResponseMessage responseMsg = new ImageResponseMessage();

		try {
			int messageType = inFromServer.readInt();
			int messageSize = inFromServer.readInt();

			if (messageSize > 1000)
				return responseMsg;

			switch (messageType) {
			case FacerecIOService.MESSAGE_TYPE_IMAGE_REPONSE:

				if (messageSize <= 36) {
					System.err
							.println("ERROR: Got an invalid image message response. Ignoring it.");
					return responseMsg;
				}

				// debug("Got response message type: " + messageType
				// + " and size: " + messageSize);

				responseMsg.detectTimeInMs = inFromServer.readInt();
				responseMsg.objectsFound = inFromServer.readInt();
				responseMsg.drawRect = inFromServer.readInt();
				responseMsg.havePerson = inFromServer.readInt();
				// read the rectangle
				FacerecRect rect = new FacerecRect();
				rect.x = inFromServer.readInt();
				rect.y = inFromServer.readInt();
				rect.width = inFromServer.readInt();
				rect.height = inFromServer.readInt();
				responseMsg.faceRect = rect;

				responseMsg.confidence = inFromServer.readFloat();

				byte[] buffer = new byte[messageSize - 36]; // 36=9 X 4 bytes
				inFromServer.read(buffer);
				responseMsg.name = byteToCharArray(buffer);

				break;

			default:
				System.err
						.println("Error: Got unknown message type back from the server ...");
				// shouldn't happen
				break;
			}
		} catch (IOException ioe) {
			ioe.printStackTrace();
		}
		return responseMsg;
	}

	private String byteToCharArray(byte[] buffer) {

		// The input buffer is null terminated and hence the we need to reduce
		// the size by 1
		char[] charBuf = new char[buffer.length - 1];
		for (int i = 0; i < buffer.length - 1; i++) {
			charBuf[i] = (char) buffer[i];
		}

		return new String(charBuf);
	}

	@Override
	protected void onSaveInstanceState(Bundle outState) {
		outState.putString(LOG_KEY, log);
		super.onSaveInstanceState(outState);
	}

	@Override
	protected void onRestoreInstanceState(Bundle savedInstanceState) {
		log = savedInstanceState.getString(LOG_KEY);
		textView.setText(log);
		super.onRestoreInstanceState(savedInstanceState);
	}

	@Override
	public void onClick(View v) {
		if (v.equals(sendButton)) {
			new SendFace().execute();
		}
	}

	public void updateLog(String text) {
		log = log + "\n" + text;
		textView.setText(log);
	}
}