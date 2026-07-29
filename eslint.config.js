const js = require("@eslint/js");

module.exports = [
  // Global ignores — must not reach across submodule boundaries
  {
    ignores: [
      "frontend/**",
      "node_modules/**",
      "contracts/**",
      "graphify-out/**",
      "fixtures/**",
      "services/**",
    ],
  },

  // Backend JavaScript files
  {
    files: [
      "server.js",
      "lib/**/*.js",
      "routes/**/*.js",
      "providers/**/*.js",
      "test/**/*.js",
      "bench/**/*.js",
    ],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "commonjs",
      globals: {
        // Node.js globals
        process: "readonly",
        require: "readonly",
        module: "readonly",
        exports: "readonly",
        __dirname: "readonly",
        __filename: "readonly",
        console: "readonly",
        Buffer: "readonly",
        setTimeout: "readonly",
        setInterval: "readonly",
        clearTimeout: "readonly",
        clearInterval: "readonly",
        setImmediate: "readonly",
        clearImmediate: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        AbortController: "readonly",
        AbortSignal: "readonly",
        fetch: "readonly",
        global: "readonly",
        queueMicrotask: "readonly",
        structuredClone: "readonly",
        TextEncoder: "readonly",
        TextDecoder: "readonly",
      },
    },
    rules: {
      ...js.configs.recommended.rules,
      "no-unused-vars": ["error", {
        argsIgnorePattern: "^_",
        caughtErrorsIgnorePattern: "^_",
        destructuredArrayIgnorePattern: "^_",
        varsIgnorePattern: "^_"
      }],
      "prefer-const": "error",
      "no-var": "error",
      eqeqeq: ["error", "always"],
    },
  },
];
