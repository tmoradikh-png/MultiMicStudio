import React from "react";
import { ActivityIndicator, View } from "react-native";
import { NavigationContainer, DefaultTheme } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StatusBar } from "expo-status-bar";
import { AuthProvider, useAuth } from "./src/context/AuthContext";
import { colors } from "./src/theme";
import type { RootStackParamList } from "./src/navigation/types";

import LoginScreen from "./src/screens/LoginScreen";
import SignupScreen from "./src/screens/SignupScreen";
import HomeScreen from "./src/screens/HomeScreen";
import CreateSessionScreen from "./src/screens/CreateSessionScreen";
import JoinSessionScreen from "./src/screens/JoinSessionScreen";
import RecordScreen from "./src/screens/RecordScreen";

const Stack = createNativeStackNavigator<RootStackParamList>();

const navTheme = {
  ...DefaultTheme,
  colors: {
    ...DefaultTheme.colors,
    background: colors.bg,
    card: colors.bg,
    text: colors.text,
    border: colors.border,
    primary: colors.primary,
  },
};

function Routes() {
  const { token, loading } = useAuth();

  if (loading) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, justifyContent: "center" }}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.bg },
        headerTintColor: colors.text,
        headerShadowVisible: false,
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      {token ? (
        <>
          <Stack.Screen
            name="Home"
            component={HomeScreen}
            options={{ headerShown: false }}
          />
          <Stack.Screen
            name="CreateSession"
            component={CreateSessionScreen}
            options={{ title: "Create" }}
          />
          <Stack.Screen
            name="JoinSession"
            component={JoinSessionScreen}
            options={{ title: "Join" }}
          />
          <Stack.Screen
            name="Record"
            component={RecordScreen}
            options={{ title: "Recording", headerBackVisible: false }}
          />
        </>
      ) : (
        <>
          <Stack.Screen
            name="Login"
            component={LoginScreen}
            options={{ headerShown: false }}
          />
          <Stack.Screen
            name="Signup"
            component={SignupScreen}
            options={{ headerShown: false }}
          />
          {/* No-account guests can join a host's session by code and record,
              without ever creating an account. */}
          <Stack.Screen
            name="JoinSession"
            component={JoinSessionScreen}
            options={{ title: "Join as guest" }}
          />
          <Stack.Screen
            name="Record"
            component={RecordScreen}
            options={{ title: "Recording", headerBackVisible: false }}
          />
        </>
      )}
    </Stack.Navigator>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <NavigationContainer theme={navTheme}>
        <StatusBar style="light" />
        <Routes />
      </NavigationContainer>
    </AuthProvider>
  );
}
