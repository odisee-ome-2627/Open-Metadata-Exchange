import type { Configuration as DevServerConfiguration } from 'webpack-dev-server';
import { BuildOptions } from './types/config';

// The FastAPI server (`docker compose up fastapi-server` publishes it on 5001).
const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:5001';

// Optional: send only the save route somewhere else, for example to the json-server mock
// (`npm run start:dev:server`, see config/mock/saveToLibrary.js) while the rest of the API
// still comes from FastAPI.
const SAVE_API_URL = process.env.REACT_APP_SAVE_API_URL;

// First match wins, so the save rule has to come before the general `/api/` rule.
const PROXY = [
  ...(SAVE_API_URL ? [{
    context: ['/api/channel/*/save'],
    target: SAVE_API_URL,
    changeOrigin: true,
  }] : []),
  {
    context: ['/api/'],
    target: API_URL,
    changeOrigin: true,
  },
];

export function buildDevServer(options: BuildOptions): DevServerConfiguration {
    return {
        port: options.port,
        open: true,
        historyApiFallback: true,
        hot: true,
        allowedHosts: "all",
        proxy: PROXY,
    };
}
