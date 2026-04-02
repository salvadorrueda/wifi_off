document.addEventListener("DOMContentLoaded", function () {
  var ROUTER_API_URL = "/api/wifi/disable";

  var minutesInput = document.getElementById("minutes");
  var disableBtn = document.getElementById("disableBtn");
  var statusDiv = document.getElementById("status");

  function showStatus(message, type) {
    statusDiv.textContent = message;
    statusDiv.className = "status " + type;
  }

  function validateMinutes() {
    var value = parseInt(minutesInput.value, 10);
    if (isNaN(value) || value < 1) {
      return null;
    }
    if (value > 1440) {
      return null;
    }
    return value;
  }

  disableBtn.addEventListener("click", function () {
    var minutes = validateMinutes();
    if (minutes === null) {
      showStatus("Introduce un valor entre 1 y 1440 minutos.", "error");
      return;
    }

    disableBtn.disabled = true;
    showStatus("Enviando solicitud al router…", "info");

    fetch(ROUTER_API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes: minutes }),
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("El router respondió con estado " + response.status);
        }
        return response.json();
      })
      .then(function () {
        showStatus(
          "WiFi desactivado durante " + minutes + " minuto" + (minutes !== 1 ? "s" : "") + ".",
          "success"
        );
      })
      .catch(function (err) {
        showStatus("Error: " + err.message, "error");
      })
      .finally(function () {
        disableBtn.disabled = false;
      });
  });
});
