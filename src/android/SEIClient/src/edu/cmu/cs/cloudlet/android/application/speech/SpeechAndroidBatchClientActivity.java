package edu.cmu.cs.cloudlet.android.application.speech;

import java.io.BufferedWriter;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileFilter;
import java.io.FileInputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.Writer;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.List;

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
import edu.cmu.cs.cloudlet.android.R;

public class SpeechAndroidBatchClientActivity extends Activity implements
		OnClickListener {
	
	public static final String LOG_KEY = "LOG_KEY";

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
				if (pathname.toString().endsWith(".wav")
						|| pathname.toString().endsWith(".WAV")) {
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
			startActivity(new Intent(SpeechAndroidBatchClientActivity.this,
					SpeechPreferenceActivity.class));
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

	/**
	 * @author gmcahill A sync task for sending audio
	 */
	class SendAudio extends AsyncTask<Void, String, String> {
		private static final String SPEECH_LOG_DIR = ".";
		ProgressDialog progreeDialog;

		@Override
		protected void onPreExecute() {
			progreeDialog = new ProgressDialog(
					SpeechAndroidBatchClientActivity.this);
			progreeDialog.setCancelable(false);

			progreeDialog.setMessage("Connecting to server...");
			updateLog("Connecting to server...");
			progreeDialog.show();
			super.onPreExecute();

		}

		@Override
		protected void onPostExecute(String result) {
			progreeDialog.dismiss();
			if (result == null) {
				updateLog("No response or error from server...");
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
			String response = null;
			Writer bufWriter = null;
			try {
				socket = new Socket();
				socket.connect(new InetSocketAddress(ipAddress, portNumber),
						5000);
				publishProgress("Connected to server "
						+ socket.getInetAddress() + " on port "
						+ socket.getPort());

				outToServer = new DataOutputStream(socket.getOutputStream());
				inFromServer = new DataInputStream(socket.getInputStream());

				SimpleDateFormat df = new SimpleDateFormat("yyyyMMdd-hhmmssSSS");
//				File f = new File(CloudletEnv.instance().getFilePath(
//						CloudletEnv.SPEECH_LOG_DIR)
//						+ File.separator
//						+ "speech_"
//						+ df.format(new Date())
//						+ ".log");
				File f = new File(SPEECH_LOG_DIR
						+ File.separator
						+ "speech_"
						+ df.format(new Date())
						+ ".log");
				bufWriter = new BufferedWriter(new FileWriter(
						f.getAbsolutePath()), 2048);
				bufWriter.write(new Date()
						+ " : ------------------ SPEECH LOG START \n");
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
					sendSpeechRequest(file);
					publishProgress("Finished sending " + file.getName());

					publishProgress("Getting response from server...");
					int responseSize = inFromServer.readInt();
					publishProgress("Response size is " + responseSize
							+ " bytes");

					if (responseSize > 0) {
						byte[] byteBuffer = new byte[responseSize];
						inFromServer.read(byteBuffer);
						responseReceivedTime = System.currentTimeMillis();
						rttForCurrentRequest = responseReceivedTime
								- requestSendTime;
						response = new String(byteBuffer);

						bufWriter.write(file.getName()
								+ "\t"
								+ requestSendTime
								+ "\t"
								+ responseReceivedTime
								+ "\t"
								+ rttForCurrentRequest
								+ "\t"
								+ Math.abs(rttForCurrentRequest
										- rttForPreviousRequest) + "\t"
								+ response + "\n");

						publishProgress("----------");
						publishProgress(response);
						publishProgress("Request Send Time: " + requestSendTime);
						publishProgress("Response Recieved Time: "
								+ responseReceivedTime);
						publishProgress("RTT Current Request: "
								+ rttForCurrentRequest);
						publishProgress("RTT Previous Request: "
								+ rttForPreviousRequest);
						publishProgress("----------");
						rttForPreviousRequest = rttForCurrentRequest;
					}
					filesProccessed++;
				}
				bufWriter.write(new Date()
						+ " : ------------------ SPEECH LOG END \n");
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

				try {
					if (inFromServer != null)
						inFromServer.close();
					if (outToServer != null)
						outToServer.close();
					if (socket != null)
						socket.close();
					if (bufWriter != null)
						bufWriter.close();
				} catch (IOException e) {
					e.printStackTrace();
				}
			}
			return response;
		}
	}

	public void sendSpeechRequest(File file) {
		try {
			int fileLength = (int) (file.length());
			outToServer.writeLong(fileLength);
			FileInputStream fis = new FileInputStream(file);
			byte[] buffer = new byte[fileLength];
			fis.read(buffer);

			outToServer.write(buffer);
		} catch (IOException io) {
			io.printStackTrace();
		}
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
			new SendAudio().execute();
		}
	}

	public void updateLog(String text) {
		log = log + "\n" + text;
		textView.setText(log);
	}
}