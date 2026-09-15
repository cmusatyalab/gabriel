package edu.cmu.cs.gabriel.client

/**
 * JUnit4 category marking tests that exercise [GabrielClient] against a real
 * [RealGabrielServer] subprocess rather than a fake. These need a Python 3
 * interpreter with the server's dependencies installed, and are excluded
 * from the default `test` task; run them with
 * `./gradlew :gabriel-client:integrationTest`.
 */
interface RealServerCategory
