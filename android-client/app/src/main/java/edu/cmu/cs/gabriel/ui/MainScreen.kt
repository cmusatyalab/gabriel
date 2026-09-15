package edu.cmu.cs.gabriel.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import edu.cmu.cs.gabriel.R
import edu.cmu.cs.gabriel.client.GabrielClient

@Composable
fun MainScreen(
    connectionState: GabrielClient.ConnectionState,
    onConnect: (endpoint: String) -> Unit,
    onDisconnect: () -> Unit,
) {
    var endpoint by remember { mutableStateOf("") }
    val connected = connectionState != GabrielClient.ConnectionState.DISCONNECTED

    Scaffold { innerPadding ->
        Column(modifier = Modifier.padding(innerPadding).padding(16.dp)) {
            OutlinedTextField(
                value = endpoint,
                onValueChange = { endpoint = it },
                enabled = !connected,
                label = { Text(stringResource(R.string.server_endpoint_hint)) },
                modifier = Modifier.fillMaxWidth(),
            )

            Button(
                onClick = { if (connected) onDisconnect() else onConnect(endpoint) },
                enabled = connected || endpoint.isNotBlank(),
                modifier = Modifier.padding(top = 16.dp),
            ) {
                Text(
                    stringResource(
                        if (connected) R.string.disconnect else R.string.connect
                    )
                )
            }

            Text(
                text = connectionState.name,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 16.dp),
            )
        }
    }
}
