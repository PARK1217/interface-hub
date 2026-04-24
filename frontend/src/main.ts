import { createApp } from 'vue';
import { createPinia } from 'pinia';
import VueApexCharts from 'vue3-apexcharts';

import App from './App.vue';
import router from './router';
import vuetify from './plugins/vuetify';

import '@mdi/font/css/materialdesignicons.css';
import 'vuetify/styles';
import '@/styles/app.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(vuetify);
app.use(VueApexCharts);
app.mount('#app');