package edu.cmu.cs.elijah.application.OR;

import android.app.AlertDialog;
import android.content.Context;
import android.util.Log;

public class Utilities {
	public static void showError(Context context, String title, String msg) {
		Log.e("krha", title + ":" + msg);
		new AlertDialog.Builder(context).setTitle(title).setMessage(msg).setPositiveButton("Confirm", null).show();
	}
}
