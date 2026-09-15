package edu.cmu.cs.gabriel

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.viewmodel.compose.viewModel
import edu.cmu.cs.gabriel.ui.MainScreen
import edu.cmu.cs.gabriel.ui.theme.GabrielClientTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            GabrielClientTheme {
                val viewModel: GabrielViewModel = viewModel()
                val connectionState by viewModel.connectionState.collectAsState()
                MainScreen(
                    connectionState = connectionState,
                    onConnect = viewModel::connect,
                    onDisconnect = viewModel::disconnect,
                )
            }
        }
    }
}
