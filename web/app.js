const $ = id => document.getElementById(id);

let current = null;
let foods = [];
let selectedImage = null;
let manualWeight = null;
let liveWeight = null;
let shownPhotoUrl = null;
let photoRequestedAt = null;  // TEMPORARY: for the "Take photo now" button

const PHOTO_SOURCES = {
  "pi-auto": "taken automatically by the scale",
  "pi-manual": "taken with the test button",
  "upload": "uploaded from this page"
};

function showScalePhoto(photo) {
  if (!photo || photo.url === shownPhotoUrl) return;
  shownPhotoUrl = photo.url;

  $("scalePhoto").src = photo.url;
  $("scalePhoto").hidden = false;

  const time = new Date(photo.ts * 1000).toLocaleTimeString();
  const parts = [time, PHOTO_SOURCES[photo.source] || photo.source];
  parts.push(photo.weight_g != null ? `logged ${photo.weight_g} g` : "nothing logged");
  if (photo.total_weight_g != null) parts.push(`scale total ${photo.total_weight_g} g`);
  $("scalePhotoInfo").textContent = parts.join(" · ");

  // Any new photo after pressing the test button counts as its answer.
  if (photoRequestedAt !== null) {
    photoRequestedAt = null;
    $("takePhoto").disabled = false;
    $("takePhotoStatus").textContent = "Photo received.";
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, options);

  if (!response.ok) {
    // FastAPI errors look like {"detail": "..."}; show just the message.
    const text = await response.text();
    let message = text;
    try { message = JSON.parse(text).detail || text; } catch (_) {}
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }

  return response.json();
}

function macroCards(n) {
  if (!n) return "";

  return [
    ["Calories", n.kcal, "kcal"],
    ["Protein", n.protein_g, "g"],
    ["Carbs", n.carbs_g, "g"],
    ["Fat", n.fat_g, "g"]
  ].map(([name, value, unit]) =>
    `<div class="macro">
       <b>${Number(value).toFixed(2)} ${unit}</b>
       <small>${name}</small>
     </div>`
  ).join("");
}

async function loadFoods() {
  foods = await api("/foods");

  // Ignore accidental empty database records.
  foods = foods.filter(f => f.class_name && f.display_name);

  $("foodSelect").innerHTML =
    '<option value="">Choose a food...</option>' +
    foods.map(f =>
      `<option value="${f.class_name}">
        ${f.display_name} (${f.state})
      </option>`
    ).join("");
}


function displayResult(result) {
  current = result;

  // Remember the original weight used for the nutrition calculation.
  const originalWeight = Number(result.weight_g);

  $("weight").textContent =
    manualWeight !== null
      ? manualWeight.toFixed(1)
      : (result.weight_g ?? "—");

  // Portion = food added for this item; total = everything on the scale.
  const total = Number(result.total_weight_g);
  $("totalWeight").textContent =
    Number.isFinite(total) && total > 0 && total !== originalWeight
      ? `Added on top of other food. Scale total: ${total.toFixed(1)} g`
      : "";

  // Apply the manually entered weight.
  if (manualWeight !== null) {
    current.weight_g = manualWeight;
  }

  const selected = result.selected_food;
  const best = result.predictions?.[0];

  $("foodName").textContent = selected
    ? `${selected.display_name} (${selected.state})`
    : best
      ? `${best.display_name} (${best.state})`
      : "No food recognised. Please select manually.";

  $("confidence").textContent = best
    ? `Recognition confidence: ${(best.confidence * 100).toFixed(1)}%`
    : "";

  let nutrition = selected?.nutrition || best?.nutrition;

  // Recalculate nutrition if the user entered a manual weight.
  if (nutrition && manualWeight !== null && originalWeight > 0) {
    const factor = manualWeight / originalWeight;

    nutrition = {
      kcal: nutrition.kcal * factor,
      protein_g: nutrition.protein_g * factor,
      carbs_g: nutrition.carbs_g * factor,
      fat_g: nutrition.fat_g * factor
    };
  }

  $("nutrition").innerHTML = macroCards(nutrition);

  $("add").disabled = !(
    result.selected_class || best?.class_name
  );

  if (!$("foodSelect").value) {
    if (result.selected_class) {
      $("foodSelect").value = result.selected_class;
    } else if (best?.class_name) {
      $("foodSelect").value = best.class_name;
    }
  }
}


async function loadMeal() {
  const meal = await api("/meal");

  $("mealItems").innerHTML = meal.items.map(item =>
    `<div class="mealrow">
       <span>
         ${item.display_name} (${item.state}) · ${item.weight_g} g
       </span>
       <button class="remove-button" data-item-id="${item.item_id}">
         Remove
       </button>
       <b>${item.kcal} kcal</b>
     </div>`
  ).join("") || '<p class="muted">No items added yet.</p>';

  $("totals").innerHTML = macroCards(meal.totals);

  document.querySelectorAll(".remove-button").forEach(button => {
    button.onclick = async () => {
      const itemId = button.dataset.itemId;

      if (!confirm("Remove this food from your meal?")) return;

      try {
        await api(`/meal/item/${itemId}`, {
          method: "DELETE"
        });

        await loadMeal();

        $("uploadStatus").textContent = "Food removed from your meal.";
      } catch (error) {
        $("uploadStatus").textContent = error.message;
      }
    };
  });
}


async function refresh() {
  try {
    const data = await api("/latest");

    $("status").textContent = "Connected";

    if (data.result) {
      displayResult(data.result);
    }

    showScalePhoto(data.photo);

    // TEMPORARY: give up waiting if the Pi never answered.
    if (photoRequestedAt !== null && Date.now() - photoRequestedAt > 20000) {
      photoRequestedAt = null;
      $("takePhoto").disabled = false;
      $("takePhotoStatus").textContent =
        "No photo arrived. Is pi_agent.py running with the camera on?";
    }

  } catch (error) {
    $("status").textContent = "Backend unavailable";
  }
}
async function refreshLiveWeight() {
  try {
    const data = await api("/weight");

    if (data.connected && Number.isFinite(Number(data.weight))) {
      liveWeight = Number(data.weight);
      $("liveWeight").textContent = liveWeight.toFixed(1);
      $("liveStatus").textContent = "Scale connected";
    } else {
      $("liveWeight").textContent = "—";
      $("liveStatus").textContent = "Waiting for scale";
      liveWeight = null;
    }
  } catch (error) {
    $("liveStatus").textContent = "Scale unavailable";
  }
}

// Open the phone camera or image gallery.
$("choosePhoto").onclick = () => {
  $("imageInput").click();
};

// Preview the selected image.
$("imageInput").onchange = event => {
  const file = event.target.files[0];

  if (!file) return;

  if (!file.type.startsWith("image/")) {
    $("uploadStatus").textContent = "Please choose an image file.";
    return;
  }

  selectedImage = file;

  $("imagePreview").src = URL.createObjectURL(file);
  $("fileName").textContent = file.name;
  $("previewArea").hidden = false;
  $("uploadStatus").textContent = "Image ready to upload.";
};

// Upload the picture and ask the backend to identify it.
$("identifyButton").onclick = async () => {
  if (!selectedImage) {
    $("uploadStatus").textContent = "Please choose a photo first.";
    return;
  }

  try {
    $("identifyButton").disabled = true;
    $("uploadStatus").textContent = "Identifying food...";

    // Get the latest weight from the scale.
    const weight = current?.weight_g ?? liveWeight;

    if (weight === undefined || weight <= 0) {
      throw new Error(
        "No valid weight found. Place food on the scale first."
      );
    }

    const formData = new FormData();
    formData.append("weight_g", weight);
    formData.append("image", selectedImage);

    const result = await api("/measurement", {
      method: "POST",
      body: formData
    });

    displayResult(result);

    $("uploadStatus").textContent =
      result.predictions?.length
        ? "Food identification completed."
        : "No prediction returned. You can select the food manually.";

    await loadMeal();

  } catch (error) {
    $("uploadStatus").textContent = error.message;
  } finally {
    $("identifyButton").disabled = false;
  }
};

// TEMPORARY test button: ask the Pi to take a photo now.
// Remove during the final cleanup (see the note in index.html).
$("takePhoto").onclick = async () => {
  try {
    $("takePhoto").disabled = true;
    await api("/photo/take", { method: "POST" });
    photoRequestedAt = Date.now();
    $("takePhotoStatus").textContent = "Asked the scale for a photo...";
  } catch (error) {
    $("takePhoto").disabled = false;
    $("takePhotoStatus").textContent = error.message;
  }
};

// Tare the scale.
$("tare").onclick = async () => {
  try {
    await api("/tare", { method: "POST" });
    $("hint").textContent = "Tare command sent to the scale.";
  } catch (error) {
    $("hint").textContent = error.message;
  }
};

// Manually correct the recognised food.
$("confirm").onclick = async () => {
  const className = $("foodSelect").value;

  if (!className) {
    $("uploadStatus").textContent = "Choose a food from the list first.";
    return;
  }

  // Use the manual weight, the measured portion, or the live scale reading.
  const weight = manualWeight ?? current?.weight_g ?? liveWeight;

  if (!(weight > 0)) {
    $("uploadStatus").textContent =
      "Set a weight first: put food on the scale or enter it manually.";
    return;
  }

  try {
    const result = await api("/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ class_name: className, weight_g: weight })
    });

    manualWeight = null;  // the weight is now stored in the result
    displayResult(result);
    $("uploadStatus").textContent = "Food selected. Press \"Add to meal\".";
  } catch (error) {
    $("uploadStatus").textContent = error.message;
  }
};

// Add the food and its measured weight to the meal.
$("add").onclick = async () => {
  if (!current) return;

  const className =
    current.selected_class ||
    current.predictions?.[0]?.class_name;

  if (!className) return;

  try {
    await api("/meal/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        class_name: className,
        weight_g: current.weight_g
      })
    });

    await loadMeal();
    $("uploadStatus").textContent = "Food added to your meal.";
  } catch (error) {
    $("uploadStatus").textContent = error.message;
  }
};

async function start() {
  try {
    await loadFoods();
    await refresh();
    await refreshLiveWeight();
    await loadMeal();

    setInterval(refresh, 1500);
    setInterval(loadMeal, 5000);
    setInterval(refreshLiveWeight, 1000);

  } catch (error) {
    $("status").textContent = "Backend unavailable";
  }
}

start();
$("setWeight").onclick = () => {
  const manualValue = $("manualWeight").value.trim();

  let weight;

  if (manualValue !== "") {
    weight = Number(manualValue);

    if (!Number.isFinite(weight) || weight <= 0 || weight > 20000) {
      alert("Enter a weight between 1 and 20000 grams.");
      return;
    }

    manualWeight = weight;
    $("hint").textContent = "Manual weight set successfully.";

  } else {
    if (liveWeight === null || liveWeight <= 0) {
      alert("No live weight available. Put food on the scale or enter a weight manually.");
      return;
    }

    weight = liveWeight;
    manualWeight = null;
    $("hint").textContent = "Live scale weight selected.";
  }

  if (!current) current = {};

  current.weight_g = weight;
  $("weight").textContent = weight.toFixed(1);
};